# NEEDS_HUMAN — cosas que necesita Ser (con el comando exacto)

## 2026-09-28 · Notion (no bloquea)
- La ficha "MPV-UOS" (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) quedó creada, pero el campo Stack solo admite
  opciones existentes (Python, uv, MCP): añadir Lua, Bash, mpv, ffmpeg, yt-dlp exigía modificar el esquema de la base y el permiso fue denegado.
  Si quieres esas etiquetas: añádelas a mano en Notion (Stack → editar opciones) o permite `mcp__claude_ai_Notion__notion-update-data-source`.
- El campo Repo está vacío porque el repo no tiene remoto. Cuando lo haya: `git remote add origin <url>` y actualizar la ficha.
- (hecho 2026-09-28 13:16) marca `~/.cache/notion-reg/mpv-uos` puesta desde la sesión interactiva; Notion al día hasta H2.

## 2026-09-28 · Opcional (no bloquea)
- "Copiar URL" del menú de TV usa `wl-copy`/`xclip`/`xsel`; no hay ninguno instalado, así que muestra la URL en pantalla.
  Para copiar de verdad: `sudo apt install wl-clipboard`

## 2026-09-29 · Opcional (no bloquea)
- "Saltar intro/créditos" necesita `fpcalc` (Chromaprint). En este portátil ya está (`/usr/bin/fpcalc` 1.6.0); en otra máquina:
  `sudo apt install libchromaprint-tools`. Sin él la función se desactiva sola (capabilities.services.intro=false).

## 2026-09-29 · Mando QR/PWA (no bloquea los tests)
- `ufw` está activo con entrada DROP, así que el móvil no llegará al mando (puerto 8790) hasta abrirlo una vez:
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos remote'` (ajusta la subred a la de tu wifi).
  Prueba a mano: `bin/mpv-uos tests/fixtures/media/chapters.mkv`, `alt+z`, escanear el QR con el móvil (misma wifi).

## 2026-09-29 · Notion sin autorizar en la sesión nocturna (no bloquea)
- El conector "claude.ai Notion" pedía autenticación y la sesión no interactiva no puede hacer OAuth: la Bitácora no recoge H12–H13
  ni el cierre del proyecto. Autorízalo en claude.ai → Ajustes → Conectores y ejecuta `/registrar` en una sesión interactiva.
- Entradas pendientes para `/registrar` (Bitácora, Fuente "Code CLI", Referencia `pc-latitude5480` + commit, Vigente):
  - `mpv-uos|2026-09-29|avance|h12-mando-qr-pwa` — commit a8532cb: mando desde el móvil (QR con alt+z, PWA servida por mpvd, cookie HMAC).
  - `mpv-uos|2026-09-29|decision|mando-qr-sin-dependencias` — ADR-032: HTTP/SSE y QR propios en Python, overlay ASS, HTTP solo en LAN.
  - `mpv-uos|2026-09-29|avance|h13-cierre` — commit 59b6c2c: tools/install.sh, docs/USO.md, README y PLATAFORMAS; backlog H0–H13 completo.
  - `mpv-uos|2026-09-29|problema|puerto-mando-ufw` — ufw bloquea el 8790; comando arriba.
  Ficha: Estado → backlog completado; Próximo paso → el "SIGUIENTE PASO" de PROGRESS.md. Después: `touch ~/.cache/notion-reg/mpv-uos`.
