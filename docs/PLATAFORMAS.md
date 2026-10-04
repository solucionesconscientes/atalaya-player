# Plataformas

Desarrollado y probado en Linux (Ubuntu, Wayland/KDE, mpv 0.41). Lo siguiente NO está probado fuera de Linux:

## Resumen por componente
| Componente | Linux | macOS | Windows |
|---|---|---|---|
| Lanzador `bin/mpv-uos` + config portable | ✅ probado | debería ir (Bash; `readlink -f` ≥ 12.3) | `bin/mpv-uos.ps1` (+ `.cmd`): probado con pwsh 7 en Linux (`-DryRun`), sin Windows real |
| Instalación de usuario `tools/install.sh` | ✅ probado (XDG, `.desktop`) | ❌ usar `bin/mpv-uos` o un alias | `tools/install.ps1` (menú Inicio, `mpv-uos://` en HKCU): descargas y verificación probadas con pwsh 7 en Linux; sin Windows real |
| AppImage (`tools/build_appimage.sh --arch x86_64\|aarch64`) | ✅ x86_64 probado sin ventana (mpv del sistema); aarch64 **cruzado desde x86-64** y revisado por dentro (runtime e intérprete ARM, catálogos), sin ejecutar | — | — |
| `.app` de macOS (`tools/build_macos_app.sh`) | construido y validado (plist, lanzador) en Linux | ❌ sin abrir en un Mac (mpv de Homebrew) | — |
| `.deb` (`tools/build_deb.sh --arch amd64\|arm64`) | ✅ amd64 probado: se extrae, arranca y mpvd se conecta con el intérprete del paquete; lintian sin más avisos que los inherentes a llevarlo dentro. arm64 cruzado y revisado por dentro | — | — |
| `.zip` portable (`tools/build_zip_windows.sh`) | — | — | construido en Linux y revisado por dentro (lanzadores, intérprete de Windows en su sitio, yt-dlp.exe); **sin abrir en un Windows** |
| ARM64 (Raspberry Pi 5) | ⚠ paquetes hechos (`.deb` arm64 y AppImage aarch64, los dos cruzados y revisados por dentro), **sin ejecutar en una máquina ARM**: aquí no hay ninguna ni emulación | — | — |
| uosc, thumbfast, scripts `mu-*` (Lua) | ✅ | debería ir (Lua puro; rutas con `utils.join_path`) | debería ir (mu-core ya distingue `.venv\Scripts\python.exe`) |
| mpvd: JSON-RPC y IPC con mpv | ✅ socket Unix | socket Unix (no probado) | named pipes (`mpvd/transport.py`, lazo Proactor) probados con un lazo simulado; sin Windows real |
| mpvd: arranque desacoplado | ✅ `start_new_session` | igual (no probado) | `DETACHED_PROCESS` + bloqueo con `msvcrt.locking` (no probado) |
| TV y radio, yt-dlp, descargas | ✅ | debería ir | `yt-dlp.exe` (lo instala y actualiza `install.ps1`/mpvd; no probado) |
| Subtítulos IA (whisper.cpp) | ✅ | `DYLD_LIBRARY_PATH` (no probado) | `install.ps1 -Whisper`: build oficial CPU `whisper-bin-x64.zip` (no probado) |
| Traducción / semántica (extras) | ✅ | wheels oficiales (no probado) | wheels oficiales (no probado) |
| Intro/créditos (`fpcalc`) | ✅ | `brew install chromaprint` | binario de acoustid.org en PATH |
| Mando QR/PWA | ✅ (abrir puerto en `ufw`) | aviso de conexiones entrantes | diálogo del Firewall de Windows |
| Salas desde internet (túnel de Cloudflare, H25) | ✅ probado con un túnel real (`cloudflared-linux-amd64` fijado en vendor.lock) | debería ir: falta añadir a vendor.lock la suma de `cloudflared-darwin-{amd64,arm64}.tgz` | debería ir: falta la suma de `cloudflared-windows-amd64.exe` y que `install.ps1` lo instale |
| Despertar para grabar y apagar al terminar (H40) | ✅ suspender/apagar con logind sin sudo; el despertador necesita **una** regla de sudoers para `rtcwake` (NEEDS_HUMAN.md) | `pmset schedule wake` + `caffeinate`: misma regla para `/usr/bin/pmset` (no probado) | tarea de usuario con `schtasks /xml` y `<WakeToRun>true</WakeToRun>` (no hace falta administrador), suspender con `rundll32 powrprof.dll,SetSuspendState`; **sin Windows aquí: sin probar**. Si el plan de energía tiene desactivados los «temporizadores de activación», la tarea no despierta y Windows no avisa: hay que mirarlo con `powercfg /q` |
| MCP (stdio) | ✅ | debería ir | debería ir (mpvd por named pipe; sin probar) |
| Decodificación por hardware (etiquetas y preferencia de códec) | ✅ `vainfo` | ❌ sin comprobar (VideoToolbox: H.264/HEVC siempre; AV1 desde M3) | ❌ sin comprobar (D3D11VA/DXVA2; `dxdiag`) |
| Convertir (ffmpeg) | ✅ VA-API (H.264; HEVC si el driver lo codifica) o CPU | CPU (sin probar); abrir carpeta con `open` | CPU (sin probar); abrir carpeta con `explorer`; sin `nice` |
| Controles del escritorio | ✅ MPRIS (D-Bus de sesión, `jeepney`) | ❌ falta `MPNowPlayingInfoCenter` | ❌ falta SMTC (`SystemMediaTransportControls`) |
| Mini reproductor / modo salón / sencillo | ✅ (Wayland: posición la decide KDE) | debería ir (sin probar) | debería ir (sin probar) |
| Mando de consola (modo salón) | ✅ `/dev/input/js*` (joydev, xpad) | ❌ falta (IOKit/GameController) | ❌ falta (XInput) |
| Mando del televisor (HDMI-CEC) | ⚠ construido contra `/dev/cec*` (API CEC del kernel), **sin probar con un televisor**: aquí no hay CEC. En una Raspberry con el driver vc4 el aparato existe; si el mando no hace nada, ver NEEDS_HUMAN.md | ❌ no hay API equivalente | ❌ no hay API equivalente |
| Compartir: ver juntos (LAN) | ✅ puerto 8791 (abrir en `ufw` como el mando) | sin probar | sin probar; cortafuegos de Windows |
| Enviar a la tele (DLNA) | ✅ con renderizador falso en tests; sin tele real probada; puerto 8792 en `ufw` | debería ir | debería ir; cortafuegos de Windows |
| Chromecast | ❌ falta (necesita `pychromecast` y un receptor para probar) | ❌ | ❌ |

## Instalar en macOS (no probado) — por Homebrew, a propósito
No hay `.dmg` ni `.pkg`, y es una decisión, no un olvido (ADR-117): un paquete de macOS **sin firmar** lo bloquea
Gatekeeper, y firmarlo exige una cuenta de desarrollador de Apple de pago, que Ser dijo expresamente que no va a
tener. Dar un `.dmg` que al abrirse dice «no se puede comprobar que no contenga malware» es peor que no darlo.
Además, el `.app` que ya se construye **no sería autocontenido**: usa el mpv de Homebrew. Así que en macOS el
camino es Homebrew y el repositorio:
```bash
brew install mpv ffmpeg uv chromaprint     # mpv ≥ 0.41
git clone <repo> Atalaya && cd Atalaya && uv sync && tools/vendor.sh
bin/mpv-uos video.mkv                        # o: alias mpv-uos="$PWD/bin/mpv-uos" en ~/.zshrc
```
`tools/build_macos_app.sh` sigue ahí y hace un `.app` que abre archivos desde Finder, para quien ya tenga el
repositorio y quiera el icono en el Dock; sin firma, la primera vez hay que abrirlo con botón derecho → *Abrir*.
Si algún día hay una cuenta de Apple, lo que falta es firmar y notarizar ese mismo `.app` y meterlo en un `.dmg`.

## Instalar en Linux: los cuatro paquetes
```bash
tools/build_deb.sh --arch amd64      # dist/atalaya-player_<versión>_amd64.deb   (27 MB)
tools/build_deb.sh --arch arm64      # dist/atalaya-player_<versión>_arm64.deb   (21 MB, cruzado)
tools/build_appimage.sh              # dist/Atalaya-x86_64.AppImage              (38 MB)
tools/build_appimage.sh --arch aarch64   # dist/Atalaya-aarch64.AppImage         (34 MB, cruzado)
```
Los dos llevan dentro la aplicación y un CPython 3.12 reubicable (el mismo tarball de python-build-standalone
para las cuatro variantes, fijado con su SHA-256 en `vendor.lock`), y **ninguno lleva mpv**: se usa el del
sistema, porque meterlo dentro rompe la aceleración por hardware (ADR-067, confirmado midiendo en ADR-115).
`Depends: mpv` **sin versión mínima**: en Debian 13 o Raspberry Pi OS el del sistema puede ser más viejo que el
probado, y la decisión es instalarse y avisar al arrancar de lo que no va, no negarse a instalar.

Lo que el `.deb` puede hacer y el AppImage no: **instalar la regla de `sudoers` del despertador**, así que con el
`.deb` las grabaciones programadas pueden encender el equipo sin que nadie toque nada. La regla autoriza un solo
programa, `/usr/lib/mpv-uos/bin/wake`, que solo sabe poner o borrar la alarma del reloj.

Cruzar a ARM se hace desde x86-64 y **el resultado no se puede ejecutar aquí** (no hay máquina ARM ni emulación):
lo comprobado es lo que llevan dentro. La primera vez que alguien los abra en una Raspberry, eso es lo que falta.

## Instalar en Windows (no probado en un Windows real)
Lo más cómodo es el **`.zip` portable** (`tools/build_zip_windows.sh`): se descomprime donde sea, lleva su propio
Python y yt-dlp, y se abre con `bin\mpv-uos.cmd`. Un `.exe` o un `.msi` sin firmar se come el aviso de SmartScreen,
que asusta más que descomprimir una carpeta (decidido con Ser el 2026-10-04). Dentro va `tools\install.ps1` para
quien quiera accesos directos y los enlaces `mpv-uos://`. mpv se instala aparte (`winget install mpv`).

### Desde un clon del repositorio
Sin permisos de administrador; el checkout se queda donde esté y todo apunta a él (nada en `%APPDATA%\mpv`).
```powershell
# antes: mpv ≥ 0.41 (build oficial de mpv.io/installation), ffmpeg/ffprobe, uv y git en el PATH
git clone <repo> Atalaya Player; cd Atalaya Player
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install.ps1 -Whisper     # -Extras: traducción y búsqueda
mpv-uos video.mkv          # o desde el menú Inicio → Atalaya Player; también bin\mpv-uos.cmd sin instalar
```
`install.ps1` comprueba mpv (≥ 0.41), ffmpeg, ffprobe y uv, ejecuta `uv sync`, descarga y verifica con SHA-256 los binarios
fijados en `vendor.lock` (`ziggy-windows.exe` de uosc, `yt-dlp.exe` contra el `SHA2-256SUMS` de su release, `deno.exe` si no hay
deno/node ≥ 22 y, con `-Whisper`, whisper.cpp CPU `whisper-bin-x64.zip`), crea `%LOCALAPPDATA%\Atalaya Player\bin\mpv-uos.cmd`,
un acceso en el menú Inicio y los enlaces `mpv-uos://` (en `HKCU\Software\Classes`). `-DryRun` enseña lo que haría sin tocar nada
(una línea JSON); `-Uninstall` lo quita todo menos los datos (`%APPDATA%\mpv-uos`) y el checkout.
El lanzador `bin\mpv-uos.ps1` pasa a mpv las mismas opciones que `bin/mpv-uos` y un `--input-ipc-server=\\.\pipe\mpv-uos-<pid>-<azar>`
por instancia; `-DryRun` imprime la orden, `-Gui` muestra los errores en una ventana (acceso directo y enlaces no tienen consola).
Ambos funcionan con Windows PowerShell 5.1 y PowerShell 7 (sin sintaxis exclusiva de la 7), pero solo se han ejecutado con
pwsh 7.6 en Linux (`tests/test_windows_scripts.py`, con un servidor HTTP local en lugar de GitHub).

## Windows
- Lanzador `bin/mpv-uos.ps1` e instalador `tools/install.ps1` (arriba). mpvd escucha en `\\.\pipe\mpv-uos-mpvd-<usuario>` y
  habla con mpv por su pipe (`mpvd/transport.py`); un segundo arranque simultáneo lo evita el bloqueo `msvcrt` de `mpvd.lock`.
- uosc incluye `bin/ziggy-windows.exe` (lo instala tools/vendor.sh). thumbfast en Windows requiere `direct_io` con LuaJIT (ver su conf).
- ffmpeg, espeak-ng y yt-dlp deben estar en PATH o en vendor/bin.
- yt-dlp: ytdl_hook necesita `yt-dlp.exe` (el zipimport con shebang no le sirve): lo instala `install.ps1` en `vendor\bin` y el
  actualizador diario de mpvd descarga en Windows ese mismo asset (`platform_asset_name()`); mu-ytdl usa `;` como separador de rutas. Runtime JS: deno `deno-x86_64-pc-windows-msvc.zip`
  (suma en vendor.lock) o node ≥ 22.
- «Abrir URL» (mu-ytdl) lee el portapapeles con la propiedad `clipboard/text` de mpv 0.41 (backend `win32`): sin probar. Si no hay
  backend, la paleta funciona igual pero sin la entrada «Pegar: …».
- Preferencias (`mu/prefs.lua`, no probado): `prefs.json` va a `%APPDATA%\mpv-uos` si no hay `MPV_UOS_DATA_DIR`; como `os.rename` no
  sobrescribe en Windows, la escritura hace borrar + renombrar (no atómica: un corte justo entre ambos deja solo `prefs.json.tmp-<pid>`)
  y la carpeta se crea con `cmd /c mkdir`. macOS: `~/Library/Application Support/mpv-uos`.

## macOS
- `readlink -f` requiere macOS ≥ 12.3 (o coreutils). `XDG_RUNTIME_DIR` no existe: el lanzador cae a `$TMPDIR/mpv-uos`.
- `hwdec=auto-safe` elige videotoolbox automáticamente; no verificado.
- yt-dlp zipimport funciona con el python3 del sistema (≥ 3.10) o el del .venv; deno para arm64/x86_64 con sumas en vendor.lock.
  Carpetas de descarga: `~/Videos`/`~/Music` (no hay user-dirs.dirs); configurable con `ytdl.settings.set`.
  El portapapeles de «Abrir URL» usa el backend `mac` de `clipboard/text` (sin probar).

## Rutas
- Config: siempre `<proyecto>/mpv-config` vía `--config-dir` (ADR-002). Caché de mpvd: `.cache/` en desarrollo; XDG/platformdirs en producción.
- cloudflared (túnel de las salas): se instala con `tools/vendor.sh` desde H51 (las salas salen a internet por
  defecto); `MU_VENDOR_CLOUDFLARED=0` lo deja fuera. Queda en
  `vendor/bin/cloudflared` (en Linux x86_64/arm64, que son las sumas que hay fijadas) y mpvd también lo acepta en
  `$MPV_UOS_CLOUDFLARED` o en el PATH. Sin él, las salas funcionan dentro de la red local.
- Grabaciones (TV, radio y tramos): `<Vídeos>/<marca>/Grabaciones`, o `MPV_UOS_RECORD_DIR`; la reserva cuando mpvd no está
  conectado es `~~desktop/<marca>` (placeholder de mpv, válido en las tres plataformas). Copiar URL usa wl-copy/xclip/xsel
  en Linux, `pbcopy` en macOS y `clip` en Windows (no probado fuera de Linux).
- Grabaciones programadas (H21): ffmpeg lanzado por mpvd y parado enviando `q` por su entrada estándar (vale en las tres
  plataformas; solo probado en Linux). Aviso de escritorio al terminar: `notify-send` en Linux, `osascript` en macOS (sin
  probar), ninguno en Windows (solo el aviso en pantalla del reproductor). Nada despierta al equipo: si está apagado o
  suspendido toda la franja, la grabación queda «perdida»; si vuelve dentro de ella, empieza tarde (ADR-049).
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
- Panel de descargas (H23): probado con Chrome headless en Linux. Abrirlo desde el menú usa `xdg-open` / `open` / `explorer`
  (macOS y Windows sin probar). Los enlaces `mpv-uos://download?url=…` solo se registran en Linux (`.desktop` de
  `tools/install.sh`); en macOS/Windows usa el marcador «Enviar al panel», que no necesita esquema. Las notificaciones del
  navegador solo salen en `http://127.0.0.1` (contexto seguro); por la red local el aviso queda en la página (y vibración en Android).

## Biblioteca y subtítulos de internet (H22)
- Escaneo con `os.walk` y rutas `pathlib`: igual en las tres plataformas (solo probado en Linux). Los fotogramas de carátula usan
  `ffmpeg`/`ffprobe` del PATH y `nice -n 10` si existe (en Windows no hay `nice`: se lanzan con prioridad normal).
- `library-secrets.json` se crea con permisos 0600; en Windows esos bits no significan nada y la protección es la de la carpeta
  del perfil del usuario (`%APPDATA%\mpv-uos`), sin ACL propias (no probado).

## Emitir en directo (H25)
Probado solo en Linux (ffmpeg 8.0, Intel iHD con `-low_power 1`). En Windows y macOS no hay VA-API: se emite por CPU
(libx264), sin probar. `live.json` se crea con modo 0600: en Windows los permisos POSIX no aplican (sin probar el acceso
por ACL). La clave se lee de la propiedad `clipboard/text` de mpv, que depende del backend de portapapeles (win32, mac,
wayland, x11); sin portapapeles nativo, configúrala con `live.configure {"key": "…"}` (MCP/CLI). En Linux la clave se ve
en `/proc/<pid>/cmdline` mientras se emite.

## Letras, audiolibros e identificar canciones (H32)
Usan ffprobe/ffmpeg/fpcalc del PATH; probado solo en Linux. En Windows, «Guardar en el archivo» (reemplazo atómico)
falla si otro programa tiene la canción abierta.

## Música (H32)
- Carpeta por defecto: en Linux la de `XDG_MUSIC_DIR` (`~/.config/user-dirs.dirs`); en Windows y macOS `~/Music` (no probado).
  `MPV_UOS_MUSIC_DIR` la sustituye (vacía = ninguna). Se añade una sola vez, la primera vez que se abre «Música».
- `ffprobe`/`ffmpeg` del PATH a la prioridad más baja (`os.nice(19)` en el hijo; en Windows prioridad normal, no probado).
- «Salida exclusiva» (`audio-exclusive`) solo la respetan algunas salidas de mpv: PipeWire, WASAPI (Windows), CoreAudio y
  AudioUnit (macOS). Con PulseAudio/ALSA mpv la ignora sin avisar. Solo probado con `--ao=null`.
- Listas M3U8 con rutas absolutas (UTF-8, `\n`); la exportación con rutas relativas usa `os.path.relpath` (en Windows falla
  entre unidades distintas y deja la ruta absoluta).
