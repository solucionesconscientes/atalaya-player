# NEEDS_HUMAN — cosas que necesita Ser (con el comando exacto)

## 2026-10-03 · De las tres que quedan, qué haría yo (y qué no)
Ser preguntó por las tres cosas que solo puede hacer él. Esto es lo que recomiendo, por orden:

1. **`rtcwake`: sí, y es la única que tapa un agujero real.** Sin esa línea, una grabación o una reproducción
   programada a las 3:00 simplemente **no ocurre**: el equipo se suspende y no se despierta. El comando exacto está
   más abajo (sección del 2026-10-01); es una regla limitada a ese binario, no un sudo general.
2. **Puerto 8790/8791 en `ufw`: NO lo abriría.** Desde H51 la sala sale a internet **por defecto** por el túnel, que
   no necesita ningún puerto abierto, y compartir es justamente con quien no está en casa. Ese puerto solo sirve
   para invitados dentro de tu propio wifi, que es el caso secundario: no vale un agujero permanente en el
   cortafuegos. Si algún día el wifi importa, el comando sigue abajo y se abre entonces.
3. **Cuenta de OpenSubtitles: sí, pero sin prisa.** Son dos minutos tuyos y da subtítulos humanos al instante en
   películas conocidas. Todo lo demás ya lo cubre whisper sin cuenta ninguna, y encima mejor en material raro.

## 2026-09-28 · Notion (no bloquea)
- La ficha "MPV-UOS" (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) quedó creada, pero el campo Stack solo admite
  opciones existentes (Python, uv, MCP): añadir Lua, Bash, mpv, ffmpeg, yt-dlp exigía modificar el esquema de la base y el permiso fue denegado.
  Si quieres esas etiquetas: añádelas a mano en Notion (Stack → editar opciones) o permite `mcp__claude_ai_Notion__notion-update-data-source`.
- El campo Repo está vacío porque el repo no tiene remoto. Cuando lo haya: `git remote add origin <url>` y actualizar la ficha.
- (hecho 2026-09-28 13:16) marca `~/.cache/notion-reg/mpv-uos` puesta desde la sesión interactiva; Notion al día hasta H2.

## 2026-09-28 · Opcional (no bloquea)
- (resuelto 2026-09-30) "Copiar URL" usa ahora el portapapeles nativo de mpv 0.41: ya no hace falta wl-clipboard.

## 2026-09-29 · Opcional (no bloquea)
- "Saltar intro/créditos" necesita `fpcalc` (Chromaprint). En este portátil ya está (`/usr/bin/fpcalc` 1.6.0); en otra máquina:
  `sudo apt install libchromaprint-tools`. Sin él la función se desactiva sola (capabilities.services.intro=false).

## 2026-09-29 · Mando QR/PWA (no bloquea los tests)
- `ufw` está activo con entrada DROP, así que el móvil no llegará al mando (puerto 8790) hasta abrirlo una vez:
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos remote'` (ajusta la subred a la de tu wifi).
  Prueba a mano: `bin/mpv-uos tests/fixtures/media/chapters.mkv`, `alt+z`, escanear el QR con el móvil (misma wifi).

## 2026-09-29 · Notion sin autorizar en la sesión nocturna (resuelto 2026-09-29: registrado desde la sesión interactiva)
- El conector "claude.ai Notion" pedía autenticación y la sesión no interactiva no puede hacer OAuth: la Bitácora no recoge H12–H13
  ni el cierre del proyecto. Autorízalo en claude.ai → Ajustes → Conectores y ejecuta `/registrar` en una sesión interactiva.
- Entradas pendientes para `/registrar` (Bitácora, Fuente "Code CLI", Referencia `pc-latitude5480` + commit, Vigente):
  - `mpv-uos|2026-09-29|avance|h12-mando-qr-pwa` — commit a8532cb: mando desde el móvil (QR con alt+z, PWA servida por mpvd, cookie HMAC).
  - `mpv-uos|2026-09-29|decision|mando-qr-sin-dependencias` — ADR-032: HTTP/SSE y QR propios en Python, overlay ASS, HTTP solo en LAN.
  - `mpv-uos|2026-09-29|avance|h13-cierre` — commit 59b6c2c: tools/install.sh, docs/USO.md, README y PLATAFORMAS; backlog H0–H13 completo.
  - `mpv-uos|2026-09-29|problema|puerto-mando-ufw` — ufw bloquea el 8790; comando arriba.
  Ficha: Estado → backlog completado; Próximo paso → el "SIGUIENTE PASO" de PROGRESS.md. Después: `touch ~/.cache/notion-reg/mpv-uos`.

## 2026-09-30 · Tras las pruebas de Ser (H14)
- **Mando por QR**: sigue haciendo falta abrir el puerto una vez (el QR y el menú del mando ya muestran esta orden con tu subred):
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos mando'`
- **Carpeta oculta de la versión anterior**: la detección de intro escribía `.mpv-uos/segments.json` junto a los vídeos; ya no lo hace.
  Queda una en `~/Descargas/Los tres días del cóndor (1975)/.mpv-uos`. Para quitarla:
  `rm -r "$HOME/Descargas/Los tres días del cóndor (1975)/.mpv-uos"`
## 2026-09-30 · Compartir (H25)
- **Túnel a internet (H25 punto 3) — HECHO el 2026-10-01** (ADR-068), ya no necesita nada de ti salvo encenderlo:
  *Compartir → Que se pueda entrar desde internet* (apagado por defecto, se recuerda). El binario no viene instalado;
  una vez:
  ```bash
  cd ~/Documentos/PROJECTES/MPV-UOS && MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh   # ya ejecutado aquí
  ```
  Probado con un túnel real: la dirección pública sirve lo que sirve el reproductor y el proceso muere con la sala.
- **Puerto de las salas**: solo hace falta si NO usas el túnel (dentro de tu wifi). `ufw` bloquea la entrada; para que
  otros equipos de tu red entren en una sala:
  `sudo ufw allow from 192.168.1.0/24 to any port 8791 proto tcp comment 'mpv-uos compartir'`

## 2026-09-30 · Plataformas (H28, no bloquea)
- **ARM64 / Raspberry Pi 5** (no hay hardware aquí). En la Pi (Raspberry Pi OS 64 bits, mpv ≥ 0.41):
  `git clone … && cd MPV-UOS && curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync --extra desktop &&
  tools/vendor.sh && tools/check.sh` y prueba el modo salón: `bin/mpv-uos` → `alt+m` → Preferencias → Modo salón.
  Si falla algo, guarda `tmp/check.log` y el `.cache/mpvd.log`.
- **AppImage en otra distribución**: `tools/build_appimage.sh` y abre `dist/Atalaya-x86_64.AppImage` en un equipo con
  otra distro (Fedora, Arch) que tenga mpv ≥ 0.41: debería abrir la pantalla de inicio y `alt+t` la TV.
- **macOS**: en un Mac con `brew install mpv uv`: clona, `uv sync --extra desktop && tools/vendor.sh &&
  tools/build_macos_app.sh` y abre `dist/MPV-UOS.app` (la primera vez: clic derecho → Abrir, no está firmado).
- **Windows** (no hay Windows aquí; los scripts solo se han ejecutado con pwsh 7 en Linux). En un Windows 10/11 con mpv ≥ 0.41,
  ffmpeg, uv y git en el PATH: clona el repo y, en PowerShell,
  `powershell -NoProfile -ExecutionPolicy Bypass -File tools\install.ps1 -Whisper` y luego `mpv-uos` (o menú Inicio → MPV-UOS):
  debería abrir la pantalla de inicio; `alt+t` la TV, un vídeo de YouTube con `ctrl+u`. Si falla, guarda la salida del
  instalador y `.cache\mpvd.log` del checkout (y la salida de `.venv\Scripts\python -m mpvd status`).

## 2026-10-01 · El despertador para grabar necesita UNA orden con sudo (H40/F4)
Suspender y apagar el equipo ya funcionan sin contraseña (logind contesta `yes` a `CanSuspend` y `CanPowerOff`), pero
**poner el despertador** necesita `rtcwake`, y `rtcwake` necesita root: comprobado aquí el 2026-10-01, da
«/dev/rtc0: Permiso denegado» y `sudo -n` pide contraseña. Con esto, una sola vez, el reproductor puede despertar el
equipo 5 min antes de una grabación programada:
```bash
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/rtcwake" | sudo tee /etc/sudoers.d/mpv-uos-rtcwake \
  && sudo chmod 0440 /etc/sudoers.d/mpv-uos-rtcwake
```
Comprobarlo después: `sudo -n rtcwake --version` (tiene que contestar sin pedir nada) y, en el reproductor,
*TV y radio → Grabaciones programadas*: la fila «Despertar el equipo 5 min antes» dejará de decir que falta algo.
Para quitarlo: `sudo rm /etc/sudoers.d/mpv-uos-rtcwake`. Es una regla **limitada a ese programa**, no un sudo general.
En macOS es lo mismo con `/usr/bin/pmset`. Mientras no esté puesto, **no se pierde ninguna grabación**: se programa
igual (si el equipo está encendido graba) y la grabación anota «sin despertador: …».

Aviso de lo que el despertador NO hace: solo despierta de la **suspensión**. Un equipo apagado del todo no se puede
encender desde el programa (eso es cosa de la BIOS/UEFI: «Wake on RTC alarm»).

## 2026-10-01 · Tu llama-server (resuelto, nada que hacer)
Me pediste pausarlo para dejar CPU libre y lo pausé (`kill -STOP`). A las 13:39 ya estaba otra vez en marcha (no lo
reanudé yo), así que no hay nada pendiente aquí. Si alguna vez lo ves parado: `kill -CONT <pid>`.

## 2026-10-01 · Subtítulos de internet (H36/C5, no bloquea)
- **Subdl descartado** (2026-10-02, decisión de Ser): ya hay dos proveedores (la web del vídeo y
  OpenSubtitles) y el tercero obligaba a escribir un cliente contra una API que no se puede comprobar
  sin clave. No hace falta que consigas nada.
- **Podnapisi queda descartado**: su dominio ya no existe. Comprobado el 2026-10-01 con el resolutor del sistema y con
  1.1.1.1: `www.podnapisi.net` y `podnapisi.net` dan NXDOMAIN. No es un corte momentáneo: no hay a dónde conectarse.
- **Cuenta de OpenSubtitles (usuario y contraseña), no solo la Api-Key**: la clave sola basta para BUSCAR, pero las
  descargas anónimas del API antiguo (`api.opensubtitles.org/xml-rpc`) devuelven un fichero de 102 bytes con un anuncio
  en vez del subtítulo (comprobado el 2026-10-01 con cuatro subtítulos distintos de *Interstellar*: `SubSize` decía
  ~155 KB y llegaban 102 B). O sea: para descargar de verdad hace falta cuenta. Ponla en *Biblioteca → Ajustes →
  Usuario / Contraseña de OpenSubtitles*; con ella el menú enseña además el cupo que te queda (200 al día por IP en el
  API antiguo, 100 al día con cuenta gratuita en el nuevo).
