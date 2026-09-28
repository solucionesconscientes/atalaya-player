# PROGRESS
ESTADO_GLOBAL: EN_CURSO
## SIGUIENTE PASO
Terminar H0: tools/check.sh en verde, commit, y pasar a H1.
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
