# Plataformas

Desarrollado y probado en Linux (Ubuntu, Wayland/KDE, mpv 0.41). Lo siguiente NO está probado fuera de Linux:

## Resumen por componente
| Componente | Linux | macOS | Windows |
|---|---|---|---|
| Lanzador `bin/mpv-uos` + config portable | ✅ probado | debería ir (Bash; `readlink -f` ≥ 12.3) | ❌ falta `bin/mpv-uos.ps1` |
| Instalación de usuario `tools/install.sh` | ✅ probado (XDG, `.desktop`) | ❌ usar `bin/mpv-uos` o un alias | ❌ pendiente (acceso directo) |
| uosc, thumbfast, scripts `mu-*` (Lua) | ✅ | debería ir (Lua puro; rutas con `utils.join_path`) | debería ir (mu-core ya distingue `.venv\Scripts\python.exe`) |
| mpvd: JSON-RPC y IPC con mpv | ✅ socket Unix | socket Unix (no probado) | ❌ necesita named pipes (`\\.\pipe\…`) en `server.py`, `client.py`, `mpvipc.py` |
| mpvd: arranque desacoplado | ✅ `start_new_session` | igual (no probado) | ❌ usar `creationflags=DETACHED_PROCESS` |
| TV y radio, yt-dlp, descargas | ✅ | debería ir | yt-dlp necesita `yt-dlp.exe` (ver abajo) |
| Subtítulos IA (whisper.cpp) | ✅ | `DYLD_LIBRARY_PATH` (no probado) | `whisper-cli.exe` + DLL (no probado) |
| Traducción / semántica (extras) | ✅ | wheels oficiales (no probado) | wheels oficiales (no probado) |
| Intro/créditos (`fpcalc`) | ✅ | `brew install chromaprint` | binario de acoustid.org en PATH |
| Mando QR/PWA | ✅ (abrir puerto en `ufw`) | aviso de conexiones entrantes | diálogo del Firewall de Windows |
| MCP (stdio) | ✅ | debería ir | depende de mpvd en Windows |

## Instalar en macOS (no probado)
```bash
brew install mpv ffmpeg uv chromaprint     # mpv ≥ 0.41
git clone <repo> MPV-UOS && cd MPV-UOS && uv sync && tools/vendor.sh
bin/mpv-uos video.mkv                        # o: alias mpv-uos="$PWD/bin/mpv-uos" en ~/.zshrc
```
Una app `.app` que abra archivos desde Finder queda pendiente (necesita un bundle con `Info.plist` que llame a `bin/mpv-uos`).

## Instalar en Windows (pendiente)
Hoy no funciona sin trabajo: falta el lanzador PowerShell y el transporte por named pipe de mpvd (ver tabla). Pasos previstos:
`winget install mpv ffmpeg astral-sh.uv`, `uv sync`, `tools/vendor.sh` desde Git Bash (con `yt-dlp.exe`), y
`bin/mpv-uos.ps1` → `mpv --config-dir=<proyecto>\mpv-config --input-ipc-server=\\.\pipe\mpv-uos-<pid>`.

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
- Nombres de países en español: en Linux, del paquete `iso-codes` (`/usr/share/iso-codes` + catálogo gettext); en Windows,
  macOS o un Linux sin él, de `mpvd/iptv/data/countries_es.json` (mismos nombres). País del usuario: `MPV_UOS_COUNTRY` o
  `LANG`/`LC_ALL`; en Windows no suelen existir, así que sale España salvo que se fije `MPV_UOS_COUNTRY` (no probado).

## whisper.cpp (H5)
- Linux: binarios y libs copiados a `vendor/whisper/bin`, ejecutados con `LD_LIBRARY_PATH` (lo añade `mpvd/asr/engine.py::library_env`).
- macOS: mismo mecanismo con `DYLD_LIBRARY_PATH` (no probado). Windows: `whisper-cli.exe` con sus DLL en la misma carpeta, sin variable
  de entorno (no probado; `find_binary` busca `vendor/whisper/bin/whisper-cli` y luego el PATH).
- Los modelos se descargan al primer directorio escribible de `MPV_UOS_WHISPER_MODELS`, `vendor/whisper/models`, `<data_dir>/models/whisper`.

## Traducción (H6)
- `ctranslate2` publica wheels para Linux x86_64/aarch64, macOS (arm64/x86_64) y Windows x86_64 con CPython 3.12; `sentencepiece` también.
  Solo se ha probado en Linux x86_64 (int8 disponible). En CPU sin AVX2 CTranslate2 cae a un kernel más lento pero funciona.

## Mando QR/PWA (H12)
- El servidor HTTP es `asyncio` puro y la IP LAN se obtiene con un socket UDP sin enviar nada: debería funcionar igual en Windows y macOS
  (no probado). En Windows el primer arranque puede mostrar el diálogo del Firewall de Windows ("Permitir acceso" en redes privadas);
  en macOS, el aviso de "aceptar conexiones entrantes" para Python. En Linux con `ufw` hay que abrir el puerto (docs/REMOTE.md).
- La PWA usa solo HTML/JS/CSS estándar y SSE; la API está probada con tests HTTP (urllib); la interfaz no se ha probado en un móvil real (Android/iOS).
