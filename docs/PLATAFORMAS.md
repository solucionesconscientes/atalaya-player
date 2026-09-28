# Plataformas

Desarrollado y probado en Linux (Ubuntu, Wayland/KDE, mpv 0.41). Lo siguiente NO está probado fuera de Linux:

## Windows
- `bin/mpv-uos` es Bash; hace falta un `bin/mpv-uos.ps1` equivalente (pendiente) que use `--input-ipc-server=\\.\pipe\mpv-uos-<pid>`.
- `mpvd/mpvipc.py` solo implementa sockets Unix; el transporte de named pipe está previsto en el diseño (misma capa de mensajes).
- uosc incluye `bin/ziggy-windows.exe` (lo instala tools/vendor.sh). thumbfast en Windows requiere `direct_io` con LuaJIT (ver su conf).
- ffmpeg, espeak-ng y yt-dlp deben estar en PATH o en vendor/bin.
- yt-dlp: el asset vendorizado (`yt-dlp`, zipimport con shebang) no sirve para ytdl_hook en Windows (busca `yt-dlp.exe`);
  tools/vendor.sh tendría que instalar `yt-dlp.exe` (SHA en docs/YTDLP.md §3) y mu-ytdl ya usa `;` como separador de rutas y
  `vendor/bin/yt-dlp.exe`. mpvd sí puede ejecutar el zipimport con su propio Python. Runtime JS: deno `deno-x86_64-pc-windows-msvc.zip`
  (suma en vendor.lock) o node ≥ 22.

## macOS
- `readlink -f` requiere macOS ≥ 12.3 (o coreutils). `XDG_RUNTIME_DIR` no existe: el lanzador cae a `$TMPDIR/mpv-uos`.
- `hwdec=auto-safe` elige videotoolbox automáticamente; no verificado.
- yt-dlp zipimport funciona con el python3 del sistema (≥ 3.10) o el del .venv; deno para arm64/x86_64 con sumas en vendor.lock.
  Carpetas de descarga: `~/Videos`/`~/Music` (no hay user-dirs.dirs); configurable con `ytdl.settings.set`.

## Rutas
- Config: siempre `<proyecto>/mpv-config` vía `--config-dir` (ADR-002). Caché de mpvd: `.cache/` en desarrollo; XDG/platformdirs en producción.
- Grabaciones de TV/radio: `~~desktop/MPV-UOS` (placeholder de mpv, válido en las tres plataformas). Copiar URL usa wl-copy/xclip/xsel
  en Linux, `pbcopy` en macOS y `clip` en Windows (no probado fuera de Linux).
