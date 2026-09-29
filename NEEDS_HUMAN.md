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
