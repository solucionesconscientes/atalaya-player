# PROGRESS
ESTADO_GLOBAL: EN_CURSO
## SIGUIENTE PASO
H6 · Sincronía, traducción y duales: (1) `mpvd/subs/` (nuevo): parser/serializador SRT/ASS→cues (reusar asr/srt.py) y `subs.resync`:
alinear un SRT externo contra las palabras/segmentos de Whisper de la tarea `asr` del archivo (DTW sobre tiempos de inicio de cue
vs. segmentos con texto similar, deriva por tramos = offset + factor por ventana) → escribe `<nombre>.resync.srt` en caché y
mu-subs lo carga con `sub-add`. (2) Traducción offline: probar en el .venv `argostranslate` (paquetes es↔en descargables) y si pesa
demasiado `ctranslate2` + opus-mt; caché por (hash, par de idiomas, modelo); servicio `subs.translate` que traduce la pista
seleccionada (SRT externo o la de la IA) → SRT traducido. (3) Duales: `secondary-sid` con el original arriba (`secondary-sub-pos`,
verificar en `mpv --list-options`) y la traducción abajo desde el menú Subtítulos IA. Verificar antes: opciones
`--sub-delay/--sub-speed`, `secondary-sid`, `sub-pos`/`secondary-sub-pos` en el mpv 0.41 instalado; `pip index versions argostranslate`.
## Registro por iteración
### Iteración 3 · 2026-09-29
#### H5 · Subtítulos IA en vivo — hecho (commit "H5: subtítulos IA en vivo")
- `tools/vendor_whisper.sh` copia whisper-cli/whisper-server + libs ggml/whisper + modelos desde el build de live-captions-linux
  (ADR-023); `vendor/whisper/VERSION`. `tools/bench_asr.sh` → docs/BENCHMARKS.md (i5-6200U, 3 hilos: base RTF 0,26–0,38, tiny 0,20,
  small-q8_0 ≈1,0; q5_1 más lentos que q8_0). Tablas de tier en `asr/models.py`: small/medium → base en vivo; large → small-q8_0.
- mpvd `asr/`: `audio.py` (WAV 16 kHz vía ffmpeg), `srt.py` (cues, fusión por trozos, SRT), `models.py` (catálogo, descarga bajo
  demanda con magia ggml, elección por tier), `engine.py` (whisper-cli por trozo, JSON, cerrojo global), `service.py` (tareas por
  archivo+modelo+idioma, plan de trozos de 20 s ordenado por distancia al cursor, `asr.seek` mueve el cursor, estado en caché de
  artefactos → reanudación instantánea, eventos push `asr` y `asr-model`). Métodos: asr.status/models/models.download/models.remove/
  start/precompute/seek/stop/segments. Solo archivos locales con duración (ADR-023).
- `mu-subs/main.lua`: menú "Subtítulos IA" (alt+i; botón CC): iniciar/detener (alt+c), idioma, modelo (descargar/borrar, progreso),
  activar automáticamente, pre-subtitular el siguiente de la lista, estado del motor. `sub-add` de la primera versión del SRT y
  `sub-reload` (≤1/s) en cada evento; `asr.seek` en seeks y cada 5 s; adopta desde caché la tarea pre-calculada del siguiente.
- mu-menu: entrada "Subtítulos IA (whisper)"; uosc.conf: `button:mu-subs`; input.conf + docs/ATAJOS.md; check.sh: paso whisper.
- Docs: docs/WHISPER.md (opciones reales, JSON, hallazgos: coste fijo del encoder de 30 s, FLAC vacío por miniaudio, tiny confunde
  idioma en `auto` con voz sintética), docs/BENCHMARKS.md, ADR-023/024, README, PLATAFORMAS.
- Tests: test_asr_engine (argv vs `--help` real, JSON, catálogo/tier, transcripción real de voz_es ≥3 palabras clave con base),
  test_asr_service (daemon: errores, tarea en vivo sobre la pista inglesa de voz_es_en.mkv, SRT sin solapes, reanudación desde caché
  y tras reiniciar mpvd, trozo bajo el cursor primero, precompute), test_mu_subs (mpv headless: pista externa añadida y seleccionada,
  `sub-text` muestra la cue tras un seek, precompute del siguiente adoptado al cambiar, menús root/idioma/modelos/estado, URL/idle
  rechazados con OSD). Se omiten si falta whisper (tools/vendor_whisper.sh).
- Latencia medida en tests (base, 3 hilos): primer trozo de 6 s listo en ≈4 s; trozo de 20 s en ≈5–6 s (RTF ≈0,3); mpv sin drops
  (headless). Pendiente de H5 ampliado: URLs/directos (grabar el audio desde la URL resuelta).
- Probar a mano:
  ```bash
  tools/vendor_whisper.sh && tools/bench_asr.sh          # binarios + tabla de RTF
  bin/mpv-uos tests/fixtures/media/voz_es_en.mkv          # alt+i → Iniciar (o alt+c); aparece la pista "Subtítulos IA (base · es)"
  .venv/bin/python -m mpvd call asr.status                # tareas, RTF, modelos; asr.models para descargar otros
  MPV_UOS_TEST_ASR_MODEL=tiny uv run pytest tests/test_asr_service.py tests/test_mu_subs.py -q
  ```
#### H5 · Subtítulos IA en vivo — plan
- Verificado en esta máquina (vendor/whisper/bin, libwhisper 1.9.3 de live-captions-linux): `whisper-cli --help` real
  (-m/-f/-t/-l/-oj/-of/-np/-tr/-bs/-bo/-nf/--prompt/--vad/--vad-model/-ml/-sow), JSON `transcription[].offsets{from,to}` en ms,
  `result.language`. El FLAC leído por miniaudio da vacío → siempre WAV vía ffmpeg (ya lo hace asr/audio.py). Coste fijo por
  llamada = encoder de 30 s (tiny 1.3 s, base 3.0 s, small-q5_1 12 s con 3 hilos, incluso con 1 s de audio): el modelo se carga
  rápido, así que whisper-server no aporta nada y se descarta (ADR-024); trozos de 20 s por defecto.
- mpv 0.41 (`--input-cmdlist`): `sub-add url [flags] [title] [lang]`, `sub-reload [id]`, `sub-remove [id]`. mu-subs localiza la
  pista por `external-filename` en `track-list` (el id puede cambiar al recargar) y recarga como mucho 1 vez/s.
- Pasos: tools/vendor_whisper.sh (copia binarios+libs+modelos desde live-captions-linux o $WHISPER_BUILD) → tools/bench_asr.sh →
  docs/BENCHMARKS.md (RTF por modelo/hilos/VAD) → ajustar tablas de tier en asr/models.py → repaso asr/service.py (chunk 20 s,
  progreso de descarga de modelos por push) → mu-subs/main.lua (menú "Subtítulos IA": iniciar/parar, idioma, modelo, descargar
  modelo, pre-subtitular el siguiente; sub-add/sub-reload; asr.seek en seek y cada 5 s; botón uosc con progreso; alt+i / alt+c)
  → tests (engine con tiny/base sobre voz_es/voz_en por palabras clave; servicio asr.* vía daemon con reanudación desde caché;
  mu-subs headless: pista externa añadida, cues correctas, precompute del siguiente) → docs (WHISPER.md, ATAJOS.md, ADR-023/024,
  PLATAFORMAS) → check.sh → commit.
### Iteración 1 · 2026-09-28
#### H0 · Cimientos — plan
- Entorno verificado: mpv 0.41.0 (Lua OK; vo gpu-next; hwdec vulkan/vaapi/nvdec), uv 0.12.14 con CPython 3.12.14 disponible,
  ffmpeg 8.0.1 (libx264, aac, libopus, flac, libmp3lame), espeak-ng 1.52 (voces es, en-us), luajit 2.1 + luacheck, yt-dlp 2026.08.19,
  node, jq, sqlite3, fpcalc, vulkaninfo/vainfo. Hardware: 4 núcleos, 23 GB RAM, sin GPU dedicada. Hostname pc-latitude5480.
- Terceros: uosc 5.13.0 (release 2026-08-03) y thumbfast master 0f711de (2026-06-28); hashes SHA-256 fijados en vendor.lock.
- Pasos: pyproject + uv (paquete mpvd, cliente IPC) → bin/mpv-uos → mpv-config (mpv.conf, input.conf, uosc, thumbfast, mu-core esqueleto)
  → tools/make_test_media.sh → tests pytest headless + tools/check.sh → README y docs/PLATAFORMAS.md.
#### H0 · Cimientos — hecho (commit "H0: cimientos")
- `pyproject.toml` (paquete `mpvd`, build uv_build, pytest + pytest-timeout), `.python-version`=3.12, `.venv` con `uv sync`.
- `mpvd/mpvipc.py`: cliente asyncio del JSON IPC de mpv (command/get/set, cola de eventos, wait_event, wait_property).
- `bin/mpv-uos`: lanza el mpv del sistema con `--config-dir=<proyecto>/mpv-config` y `--input-ipc-server=$XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock`;
  exporta `MPV_UOS_ROOT` y `MPV_UOS_SOCKET`.
- `mpv-config/`: mpv.conf (gpu-next, auto-safe, save-position-on-quit, osc=no, osd-bar=no…; cada clave validada por test), input.conf con
  menú uosc (`#!`), uosc 5.13.0 + thumbfast 0f711de vendorizados (`vendor.lock`, `tools/vendor.sh`, ziggy fuera de git), `mu-core.lua` esqueleto
  que publica `user-data/mu/core` y detecta uosc.
- `tools/make_test_media.sh`: video30.mkv, chapters.mkv (3 capítulos), voz_es/voz_en.flac (espeak-ng, 16 kHz mono, JSON con frases y
  palabras clave), voz_es_en.mkv (2 pistas de audio spa/eng), manifest.json.
- `tools/check.sh`: entorno → vendor → medios → luacheck → shell lint → pytest (sin red) → pytest -m network si hay conectividad.
- Tests (16): launcher, opciones de mpv.conf contra `--list-options`, input.conf sin errores, vendor.lock ↔ uosc instalado, smoke headless
  (uosc/thumbfast/mu-core cargan sin errores Lua, uosc registra bindings, reproduce capítulos y pistas), cliente IPC, medios ↔ manifest.
- Probar a mano:
  ```bash
  tools/check.sh                                   # todo en verde
  bin/mpv-uos tests/fixtures/media/chapters.mkv    # ventana con uosc en español; botón derecho = menú
  bin/mpv-uos --vo=null --ao=null --idle=yes --input-ipc-server=tmp/a.sock &   # headless
  echo '{"command":["get_property","user-data/mu/core"]}' | socat - UNIX-CONNECT:tmp/a.sock   # o con python: mpvd.mpvipc
  ```
- Pendiente de H0: registro en Notion (delegado a subagente en esta iteración).
#### H1 · mpvd núcleo — plan
- Transporte: mpv Lua no tiene sockets, así que mu-core NO mantiene conexión; lanza `python -m mpvd ensure --attach <ipc>` (subproceso corto,
  asíncrono) que arranca el daemon si no responde y registra la sesión. Después es **mpvd quien se conecta al IPC de mpv** (cliente JSON IPC)
  y desde ahí: recibe peticiones JSON-RPC que los scripts envían con `script-message mu-rpc <json> [script-destino]` (evento `client-message`),
  responde con `script-message-to <script> mu-reply <json>`, observa propiedades (frame-drop-count, pause) para el guardián y detecta el cierre.
- Módulos: config (rutas runtime/caché dev vs XDG), rpc (JSON-RPC 2.0 con batch y errores estándar), server (socket Unix, pidfile, idle-timeout),
  sessions (una por mpv), hashing (OpenSubtitles + blake2b de tamaño+64 KB inicio/fin), cache (SQLite + blobs, claves
  (hash, artefacto, modelo, versión, parámetros)), jobs (cola con prioridades URGENT/INTERACTIVE/PRECOMPUTE/INDEX, cancelación, progreso),
  guardian (tasa de frames perdidos → frena trabajos pesados), client + CLI (serve/ensure/call/status/stop).
- mu-core.lua: opciones (script-opts mu-core-*), ensure con reintentos/backoff, `mu-hello`/`mu-reply`, API `rpc(method, params, cb)`,
  watchdog (ping periódico), `mu-call` para pruebas que guarda en `user-data/mu/last_reply`, estado en `user-data/mu/core`.
- Tests: unit (rpc, hashing, cache, jobs, guardian, server in-process) + integración (mpv headless → mu-core arranca mpvd → sesión
  registrada → ping ida y vuelta → hash de archivo → shutdown del daemon → reconexión por watchdog → cierre limpio).
- Notion: ficha creada (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) y Bitácora `mpv-uos|2026-09-28|avance|h0-cimientos`.
  Slug añadido a CLAUDE.md. Detalles pendientes para Ser en NEEDS_HUMAN.md (Stack, Repo, marcador ~/.cache/notion-reg).
#### H1 · mpvd núcleo — hecho (commit "H1: mpvd núcleo")
- `mpvd/`: `rpc.py` (JSON-RPC 2.0, batch, códigos estándar), `server.py` (socket Unix 0600, pidfile, socket viejo limpiado, idle-timeout),
  `sessions.py` (una conexión IPC por mpv, hello, observe_property, mu-rpc/mu-reply), `methods.py` (ping, version, capabilities,
  shutdown, sessions.*, jobs.*, guardian.*, file.hash, cache.*), `jobs.py` (cola con prioridades urgent/interactive/precompute/index,
  cancelación, progreso, historial), `guardian.py` (tasa de frame-drop-count > 2/s → frena trabajos `heavy` 10 s), `hashing.py`,
  `cache.py` (SQLite WAL + blobs, get/put/invalidate/stats/prune LRU), `hardware.py` (tier small/medium/large), `client.py`,
  `__main__.py` (serve / ensure / call / status / stop), `config.py` (rutas dev .cache vs XDG).
- `mu-core.lua` 0.2.0: opciones `mu-core-*`, ensure con backoff, `mu-hello`, `rpc()` con timeouts, watchdog, `mu-call` (guarda en
  `user-data/mu/last_reply`), estado en `user-data/mu/core`.
- Tests (59 en total): rpc, hashing (referencia independiente), cache, jobs+guardian, server in-process, e integración real:
  mpv headless → mu-core arranca mpvd → sesión registrada con pid → ping/file.hash/sessions.current ida y vuelta → observa `path` →
  trabajo cancelado al cerrar mpv → watchdog reconecta tras `shutdown` → CLI `ensure` idempotente.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv           # mu-core arranca mpvd solo
  .venv/bin/python -m mpvd status                        # sesiones, trabajos, guardián
  .venv/bin/python -m mpvd call capabilities             # métodos disponibles
  .venv/bin/python -m mpvd call jobs.sleep '{"seconds":3,"heavy":true}' && .venv/bin/python -m mpvd call jobs.list
  .venv/bin/python -m mpvd call file.hash '{"path":"tests/fixtures/media/video30.mkv"}'
  .venv/bin/python -m mpvd stop                          # mu-core lo relanza en ≤30 s (watchdog)
  tail -f .cache/mpvd.log
  ```
- Dentro de mpv (consola `): `script-message-to mu_core mu-call ping` y leer `user-data/mu/last_reply`.
#### H2 · TV y radio — plan
- Verificado en mpv 0.41: `http-header-fields` (lista), `user-agent`, `referrer`, `tls-verify`, `stream-lavf-o`, `stream-record=<archivo>`,
  `loadfile <url> replace -1 {opciones por archivo}` (índice -1 obligatorio desde 0.38), `metadata/by-key/<k>` para el título ICY.
- Fuentes reales y fixtures: subagente descarga TDTChannels (tv/radio/tvradio), iptv-org (index + índices por país/categoría/idioma + API
  JSON) y Radio Browser; deja docs/FUENTES_IPTV.md y tests/fixtures/iptv/. Menu API de uosc 5.13 verificada en docs/UOSC_API.md.
- mpvd: `net.py` (caché HTTP con ETag/Last-Modified, TTL, offline) → `iptv/m3u.py` (parser tolerante) → `iptv/model.py` (canal
  normalizado + cabeceras → opciones mpv) → `iptv/sources.py` (fuentes configurables, refresco) → `iptv/index.py` (búsqueda sin acentos)
  → `iptv/radiobrowser.py` → `iptv/store.py` (favoritos/recientes en data_dir) → métodos RPC `iptv.*` → salud opcional (ffprobe, INDEX).
- mu-iptv.lua: menú uosc "TV y radio" (España TV/radio, Mundo por país→categoría, Radio mundial, Favoritos, Recientes, Buscar), botón en
  controles, reproducción con opciones por archivo, zapping ±1 en el grupo, OSD con nombre, ICY en pantalla, grabación (stream-record).
#### H2 · TV y radio — hecho (commits "H2 (parte 1)" y "H2 (parte 2)")
- Backend (parte 1): `mpvd/net.py` caché HTTP (ETag/Last-Modified, TTL 12 h, offline con la última copia), `iptv/m3u.py` parser tolerante
  (tvg-*, group-title, url-tvg, #EXTVLCOPT, #KODIPROP, comillas escapadas, HLS vs lista), `iptv/model.py` canal normalizado + cabeceras →
  opciones de mpv por archivo + sufijos iptv-org `(720p)`/`[Geo-blocked]`/`[Not 24/7]` a campos, `iptv/index.py` búsqueda sin acentos,
  `iptv/store.py` favoritos/recientes/listas de usuario/salud (SQLite), `iptv/sources.py` fuentes (TDTChannels tv+radio, iptv-org) y
  `iptv/service.py` métodos `iptv.*` (sources, refresh, facets, countries, channels, search, channel, play, zap, favorites.*, recents.*,
  sources.add/remove, health.check/status).
- Parte 2 (esta sesión, interactiva): `iptv/radiobrowser.py` (Radio Browser: países, tags, por país, búsqueda, top, contador de clics) con
  métodos `radio.*`; `mpv-config/scripts/mu-iptv/main.lua` + `script-modules/mu/{rpc,uosc}.lua`: menú uosc "TV y radio" (España TV/Radio,
  Mundo país→categoría con nombres y banderas de la API de iptv-org, Radio mundial, Favoritos, Recientes, Mis listas, Buscar en paleta),
  acciones por ítem (favorito, copiar URL, quitar lista), botón `mu-tv` en la barra de uosc, reproducción con opciones por archivo,
  zapping ±1 dentro del grupo, OSD con nombre y título ICY, grabación `stream-record` a `~~desktop/MPV-UOS`, comprobación de salud en
  segundo plano desde la lista. Teclas en input.conf: alt+t menú, alt+f buscar, alt+UP/DOWN zapping, alt+r grabar.
- Correcciones de esta sesión: el estado de navegación sigue a `user-data/uosc/menu/type` leído en nativo (la forma string de una
  sub-clave de user-data es JSON con comillas, por lo que las comparaciones con el tipo nunca acertaban y cada `show()` reabría el menú)
  y ya no depende del evento `close`, que uosc emite desde su hilo en mitad de un reemplazo; los tests aíslan `MPV_UOS_DATA_DIR` por
  ejecución (antes compartían `.cache/data` y un favorito de una pasada se desmarcaba en la siguiente); `wait_property` tolera
  `property not found` en user-data; límite de línea JSON de IPC/JSON-RPC a 32 MiB;
  filas compactas con `geo_blocked`/`not_24_7`; test de red de iptv-org usa el país del probador (MPV_UOS_TEST_COUNTRY, por defecto es)
  y registra como xfail si ningún stream arranca.
- Tests: parser con fixtures reales recortadas (tests/fixtures/iptv), servicio con servidor HTTP local, Radio Browser simulado,
  integración headless mu-iptv (menús, reproducción, zapping, favoritos, grabación real de un directo por HTTP, búsqueda, mundo, radio,
  Mis listas + salud) y @network (descarga de listas reales, directorio Radio Browser, reproduce 3+3+3 canales reales).
- Probar a mano:
  ```bash
  bin/mpv-uos --idle=yes            # alt+t → TV y radio; alt+f busca; Tab sobre un canal → ★ / copiar URL
  .venv/bin/python -m mpvd call iptv.refresh                      # descarga/actualiza listas (caché 12 h)
  .venv/bin/python -m mpvd call iptv.search '{"q":"antena 3","compact":true}'
  .venv/bin/python -m mpvd call iptv.countries '{"source":"iptv_org"}'
  .venv/bin/python -m mpvd call radio.stations '{"country":"es","limit":5,"compact":true}'
  .venv/bin/python -m mpvd call iptv.sources.add '{"name":"Mi lista","url":"https://ejemplo/lista.m3u"}'
  MPV_UOS_TEST_COUNTRY=es uv run pytest -m network tests/test_network_iptv.py -s   # reproduce canales reales
  ```
- Runner nocturno: `tools/nocturno.sh` espera al reset del cupo de sesión de Claude (hora del mensaje o 30 min) sin contarlo como fallo,
  tope de 3 h por iteración, effort `high` (.runner.env) y `DEADLINE=07:30`. Lanzamiento: `tmux kill-session -t mpvuos;
  tmux new-session -d -s mpvuos "cd <proyecto> && systemd-inhibit --what=sleep:idle bash tools/nocturno.sh; exec bash"`.
#### H5 · Subtítulos IA en vivo — plan
- Notion al día (2026-09-29): avances H3/H4, decisiones ADR-019/021 y Próximo paso en la ficha (vía subagente).
- Verificación (subagente → docs/WHISPER.md): whisper.cpp de ~/proyectos/live-captions-linux (copiado a vendor/whisper si sirve,
  si no compilado desde un tag estable), `--help` real de whisper-cli/whisper-server, VAD Silero, formato JSON, RTF por modelo.
- Ya hecho sin depender del modelo: `mpvd/asr/audio.py` (ventana WAV 16 kHz mono con ffmpeg, verificado) y `mpvd/asr/srt.py`
  (segmentos, SRT incremental, fusión por trozos con recorte de solapes) + tests.
- Siguiente: `asr/engine.py` (whisper-server persistente con fallback a whisper-cli), `asr/service.py` (`asr.*`: start/seek/stop/
  status/models; trozos de 10 s con solape, prioridad URGENT para el trozo por delante de time-pos, PRECOMPUTE para el resto y
  el siguiente de la playlist; SRT en caché por hash; eventos push `asr`), `mu-subs.lua` (menú "Subtítulos IA": iniciar/parar,
  idioma, modelo; `sub-add` la primera vez y `sub-reload` en cada actualización; `asr.seek` en seeks; progreso en uosc),
  tools/bench_asr.sh → docs/BENCHMARKS.md, tests con el modelo tiny sobre voz_es/voz_en (palabras clave del manifest).
- Iteración 2 (2026-09-29): binarios de whisper.cpp ya copiados en vendor/whisper (de live-captions-linux, libwhisper 1.9.3,
  funcionan con LD_LIBRARY_PATH); la compilación de vendor/whisper-src (v1.9.4) murió por tiempo → se descarta (ADR-023).
  Diseño: un solo trabajo `heavy` por archivo que recorre un plan de trozos ordenado por distancia a time-pos (seek = mover el
  cursor, sin cancelar trabajos); semáforo global de 1 proceso whisper; SRT estable en .cache/asr/<clave>/ + estado en la caché
  de artefactos (reanudable); modelos bajo demanda desde Hugging Face con verificación de magia ggml; eventos push a mu_subs.
#### H4 · UX base — hecho (commit "H4: UX base")
- mpvd `watch.py` (servicio `watch.*`: get/update/recents/search/remove/clear; clave por contenido; historial de 500 entradas).
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m, botón `mu-menu` en la barra; "Continuar viendo" inline),
  vista Recientes (alt+h; Tab olvida; borrar historial), paleta global (alt+p; comandos de `input-bindings` + curados, canales,
  recientes, acciones de mpvd), continuar viendo (seek al cargar si procede; guarda cada 15 s/pausa/seek/fin; directos excluidos),
  pantalla de inicio en idle. mu-iptv: `mu-iptv-play <id>` y `current_url`. uosc.conf: botones `mu-menu` y `mu-ytdl`.
- docs/ATAJOS.md completo + tests/test_atajos.py (cada tecla de input.conf documentada y cada script-binding existente).
- Tests: test_watch.py (store, clave estable al renombrar, métodos), test_mu_menu.py (reanudación tras renombrar, no reanuda lo
  terminado, menú raíz con recientes, paleta con comandos/canales/recientes/acciones, reproducir canal desde la paleta, pantalla
  de inicio). conftest: `start_screen` en start_mpv (desactivada por defecto).
- Probar a mano:
  ```bash
  bin/mpv-uos                                  # pantalla de inicio (recientes + accesos); alt+p paleta; alt+m menú
  bin/mpv-uos tests/fixtures/media/video30.mkv # avanza a 0:25, cierra con q; cópialo con otro nombre y ábrelo: reanuda en 0:25
  .venv/bin/python -m mpvd call watch.recents
  .venv/bin/python -m mpvd call watch.search '{"q":"video"}'
  ```
#### H4 · UX base — plan
- Verificado: `input-bindings` (mpv 0.41) devuelve `{key, cmd, comment, section, priority, is_weak}`; los comentarios `#!` de
  input.conf llegan como `comment = "! Título > Sub"` → base de la paleta de comandos. `idle-active` + `playlist-count` para la
  pantalla de inicio. `--script-opts=` posterior sustituye la lista entera (usar `--script-opts-append` en tests).
- mpvd `watch.py` (servicio `watch.*`): SQLite en data_dir con clave por contenido (`file.hash` para archivos, `url:` para URLs):
  get/update/recents/search/remove/clear; "terminado" si posición ≥ duración-30 s o ≥95 %.
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m; botón `mu-menu` primero en la barra; incluye "Menú completo"
  = uosc/menu), paleta global (alt+p: comandos de input-bindings + lista curada, canales vía iptv.search, recientes vía
  watch.search, acciones de mpvd), continuar viendo por hash (seek en file-loaded si mpv no reanudó ya; posición cada 15 s,
  en pausa/seek/end-file), pantalla de inicio en idle (opción `mu-menu-start_screen`, desactivada en tests salvo el suyo).
- mu-iptv gana `script-message mu-iptv-play <id>` para la paleta. uosc.conf: `button:mu-menu` y `button:mu-ytdl` en controls.
- docs/ATAJOS.md + test que comprueba que todas las teclas de input.conf están documentadas.
#### H3 · yt-dlp avanzado — hecho (commit "H3: yt-dlp avanzado")
- Verificación real: docs/YTDLP.md (release 2026.08.19, assets y SHA, runtime JS/EJS, `--help` completo, `-J`, progreso JSON,
  fixtures) y docs/MPV_YTDL.md (ytdl_hook embebido en mpv 0.41: opciones, lectura en caliente de script-opts, `user-data/mpv/ytdl/*`,
  sintaxis `loadfile … replace -1 {…}`, pruebas headless).
- Vendorizado: `vendor.lock` fija `yt-dlp` 2026.08.19 (zipimport, SHA-256) y las sumas de deno v2.9.7 por plataforma;
  `tools/vendor.sh` instala vendor/bin/yt-dlp (no pisa una versión más nueva instalada por mpvd) y deno solo si no hay runtime JS.
- mpvd `ytdl/`: `binary.py` (resolución env → vendor → PATH, runtime JS deno/node≥22, ffmpeg, updater diario verificado con
  SHA2-256SUMS), `info.py` (filas de formato con etiqueta/hint, agrupación combinado/vídeo/audio, resumen, playlists planas),
  `presets.py` (DownloadSpec → argv exacto; 14 presets; política de contenedor ADR-019), `downloads.py` (cola sobre JobQueue,
  progreso `MU_PROGRESS`/`MU_PP`/`MU_DONE`, cancelar/repetir/quitar, historial y ajustes persistentes, carpetas XDG),
  `service.py` (métodos `ytdl.status/hook/update.check/update.apply/info/playlist/presets/download/downloads.*/settings.*`,
  caché de `-J` por URL con semilla del hook, eventos push a todas las sesiones). `sessions.push_event` genérico.
- `mu-ytdl/main.lua`: fija `ytdl_hook-ytdl_path` y `ytdl-raw-options js-runtimes` en caliente; conmutador vídeo/solo audio
  (alt+a) en la misma posición; menú Calidad (alt+q) con cambio en caliente y acción "descargar este formato"; menú Descargar
  (alt+d) con presets y opciones conmutables; panel Descargas (alt+l) en vivo desde `mu-event`; Estado de yt-dlp con
  actualización manual; botón ⬇ en la barra con contador; `user-data/mu/ytdl` con estado e items del menú para tests.
- Tests: presets (argv exacto), info (fixtures reales de YouTube/archive.org/playlists), binario+updater (servidor HTTP local,
  SHA incorrecto rechazado), descargas y métodos con un yt-dlp falso (`tests/fixtures/ytdlp/fake_ytdlp.py`), integración headless
  mu-ytdl (ytdl_hook real ejecutando el fake, medios por HTTP, conmutador, calidad, descarga, panel) y @network (info real,
  comprobación en GitHub, descargas reales 360p/mp3 128k/mkv verificadas con ffprobe). conftest aísla watch_later/resume.
- Probar a mano:
  ```bash
  bin/mpv-uos https://www.youtube.com/watch?v=aqz-KE-bpKQ   # alt+q calidad · alt+a solo audio · alt+d descargar · alt+l descargas
  bin/mpv-uos https://archive.org/details/Countdow1960
  .venv/bin/python -m mpvd call ytdl.status
  .venv/bin/python -m mpvd call ytdl.update.check '{"force":true}'
  .venv/bin/python -m mpvd call ytdl.download '{"url":"https://archive.org/details/Countdow1960","preset":"video_360"}'
  .venv/bin/python -m mpvd call ytdl.downloads.list
  uv run pytest -m network tests/test_network_ytdl.py -s
  ```
- Test intermitente conocido: `test_mu_iptv.py::test_menus_play_zap_favorites_record_and_search` puede agotar el tiempo de
  espera del directo (ffmpeg -re) con la máquina cargada; se subió a 90 s. Pasa aislado.
#### H3 · yt-dlp avanzado — plan (original)
- Verificación previa (subagentes): docs/YTDLP.md (última release, assets, runtime JS para YouTube, opciones reales de `--help`,
  formato `-J`, `--progress-template`) y docs/MPV_YTDL.md (ytdl_hook de mpv 0.41: script-opts, `ytdl_path`, `all_formats`,
  `loadfile ... replace -1 {ytdl-format=…,start=…}`, `vid=no` + `audio-display`). Fixtures reales de `-J` en tests/fixtures/ytdlp/.
- Vendorizado: vendor.lock gana `YTDLP_VERSION/URL/SHA256` (asset `yt-dlp` zipimport, corre con el Python del .venv); tools/vendor.sh lo
  instala en vendor/bin/yt-dlp (+ envoltorio ejecutable). mpvd comprueba una vez al día (`ytdl.update.check`) la última release en
  GitHub (caché HTTP) y la descarga verificando el SHA2-256SUMS oficial; nunca en el hilo de mpv. Runtime JS (deno) vendorizado en
  vendor/bin si YouTube lo exige (verificar en docs/YTDLP.md).
- ytdl_hook: `script-opts/ytdl_hook.conf` con `ytdl_path=<ruta absoluta>` no vale (la config debe ser portable) → mu-ytdl fija
  `ytdl_hook-ytdl_path` en caliente vía `script-opts` al arrancar apuntando a vendor/bin (relativo a MPV_UOS_ROOT).
- mpvd `ytdl/`: `binary.py` (localizar/actualizar binario), `info.py` (`-J` con caché por URL, agrupación de formatos), `presets.py`
  (argumentos de cada preset: exacto/combinación/audio original/convertido/opciones extra), `downloads.py` (cola con progreso
  `--newline --progress-template`, cancelar, reintentar, carpetas XDG y plantilla), `service.py` (métodos `ytdl.*`).
- mu-ytdl.lua: conmutador vídeo/solo audio (tecla + menú, `loadfile … replace -1 {ytdl-format=…,start=<pos>}` conservando pausa,
  velocidad y volumen), menú "Calidad" (todos los formatos agrupados) y "Descargar" (presets), panel "Descargas" con progreso
  (eventos push `mu-event` desde mpvd), botón en la barra de uosc y teclas en input.conf.
- Tests: unit (presets → argv exacto, parseo de `-J` desde fixtures, parser de progreso, comprobación de actualización con servidor
  local), integración headless (mu-ytdl con un yt-dlp falso que devuelve el JSON de fixture y "descarga" un archivo local con
  progreso), @network (descarga real corta en 2 presets verificada con ffprobe).
