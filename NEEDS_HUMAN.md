# NEEDS_HUMAN — cosas que necesita Ser (con el comando exacto)

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
- **Desentrelazado con ventana real** (no se puede comprobar sin pantalla): abre 7TV Andalucía (emite entrelazado sin marcarlo),
  pulsa `d` y comprueba que desaparecen las rayas horizontales en movimiento. Si no, dímelo con el log:
  `mpv-uos --deinterlace=yes --msg-level=autofilters=v,vf=v --log-file=/tmp/deint.log <URL del canal>`

## 2026-09-30 · Compartir (H25)
- **Túnel a internet (H25 punto 3) — [~] bloqueado**: el modo de permisos de la sesión nocturna denegó arrancar un túnel
  de entrada (Cloudflare quick tunnel), aunque sea solo con la sala abierta. Necesita tu permiso explícito. Si lo quieres:
  en una sesión interactiva di «implementa el túnel de H25 con cloudflared» (o añade una regla de permiso para
  `vendor/bin/cloudflared tunnel --url http://127.0.0.1:8791`). Hasta entonces las salas son solo de tu red local.
- **Puerto de las salas**: como el mando, `ufw` bloquea la entrada; para que otros equipos de tu wifi entren en una sala:
  `sudo ufw allow from 192.168.1.0/24 to any port 8791 proto tcp comment 'mpv-uos compartir'`

## 2026-09-30 · Plataformas (H28, no bloquea)
- **ARM64 / Raspberry Pi 5** (no hay hardware aquí). En la Pi (Raspberry Pi OS 64 bits, mpv ≥ 0.41):
  `git clone … && cd MPV-UOS && curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync --extra desktop &&
  tools/vendor.sh && tools/check.sh` y prueba el modo salón: `bin/mpv-uos` → `alt+m` → Preferencias → Modo salón.
  Si falla algo, guarda `tmp/check.log` y el `.cache/mpvd.log`.
- **AppImage en otra distribución**: `tools/build_appimage.sh` y abre `dist/MPV-UOS-x86_64.AppImage` en un equipo con
  otra distro (Fedora, Arch) que tenga mpv ≥ 0.41: debería abrir la pantalla de inicio y `alt+t` la TV.
- **macOS**: en un Mac con `brew install mpv uv`: clona, `uv sync --extra desktop && tools/vendor.sh &&
  tools/build_macos_app.sh` y abre `dist/MPV-UOS.app` (la primera vez: clic derecho → Abrir, no está firmado).
