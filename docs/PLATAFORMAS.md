# Plataformas

Desarrollado y probado en Linux (Ubuntu, Wayland/KDE, mpv 0.41). Lo siguiente NO está probado fuera de Linux:

## Windows
- `bin/mpv-uos` es Bash; hace falta un `bin/mpv-uos.ps1` equivalente (pendiente) que use `--input-ipc-server=\\.\pipe\mpv-uos-<pid>`.
- `mpvd/mpvipc.py` solo implementa sockets Unix; el transporte de named pipe está previsto en el diseño (misma capa de mensajes).
- uosc incluye `bin/ziggy-windows.exe` (lo instala tools/vendor.sh). thumbfast en Windows requiere `direct_io` con LuaJIT (ver su conf).
- ffmpeg, espeak-ng y yt-dlp deben estar en PATH o en vendor/bin.

## macOS
- `readlink -f` requiere macOS ≥ 12.3 (o coreutils). `XDG_RUNTIME_DIR` no existe: el lanzador cae a `$TMPDIR/mpv-uos`.
- `hwdec=auto-safe` elige videotoolbox automáticamente; no verificado.

## Rutas
- Config: siempre `<proyecto>/mpv-config` vía `--config-dir` (ADR-002). Caché de mpvd: `.cache/` en desarrollo; XDG/platformdirs en producción.
