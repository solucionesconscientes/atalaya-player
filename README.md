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
tools/vendor.sh      # descarga y verifica uosc 5.13.0 (+ ziggy) y thumbfast según vendor.lock
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

## Estructura
- `bin/mpv-uos` lanzador · `mpv-config/` configuración portable (mpv.conf, input.conf, scripts `mu-*`, uosc, thumbfast)
- `mpvd/` daemon Python (JSON-RPC 2.0) · `tests/` pytest (unit + integración con mpv headless) · `tools/` scripts de build/check
- `vendor.lock` versiones fijadas de terceros · `vendor/` descargas (ignorado por git)

## Tests a mano
```bash
uv run pytest                     # sin red
uv run pytest -m network          # solo los que necesitan internet
tools/make_test_media.sh          # regenera tests/fixtures/media (ffmpeg + espeak-ng)
```
