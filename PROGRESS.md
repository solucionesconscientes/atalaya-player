# PROGRESS
ESTADO_GLOBAL: EN_CURSO
## SIGUIENTE PASO
H3 · yt-dlp avanzado: empezar por vendorizar yt-dlp en vendor/bin con comprobación diaria de actualización (tools/vendor.sh),
verificar con `vendor/bin/yt-dlp --help` las opciones reales (-J, -f, --remux-video, --extract-audio, --audio-format/--audio-quality,
--embed-*, --sponsorblock-*), y configurar ytdl_hook para usar ese binario. Luego conmutador vídeo/solo audio y menú Calidad.
## Registro por iteración
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
