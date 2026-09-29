# Servidor MCP de MPV-UOS (`python -m mpvd mcp`)

Servidor MCP por **stdio** implementado a mano en `mpvd/mcp.py` (sin dependencias: el SDK `mcp` 2.2 arrastra ~30 paquetes, ADR-028).
Subconjunto de la especificación 2025-06-18 que usan los clientes: `initialize`, `notifications/initialized`, `ping`, `tools/list`,
`tools/call`, `resources/list`, `resources/read`, `resources/templates/list`, `prompts/list`. Un objeto JSON por línea.

## Configuración del cliente
- Claude Code: copia `.mcp.json.example` a `.mcp.json` en el proyecto (o en el directorio desde el que trabajes) y ajusta las rutas.
- Claude Desktop y otros: mismo bloque en su `claude_desktop_config.json`.
- El servidor arranca mpvd si no está (`ensure_daemon`) y habla con él por el socket JSON-RPC; controla la instancia de mpv más
  reciente (o `--session <id>`). Logs por stderr, nunca por stdout.
- Confirmaciones: `play` (si algo se está reproduciendo), `seek`, `play_channel` y `download` abren un diálogo uosc "Sí, adelante / No"
  en la pantalla del reproductor (mu-menu `mu-confirm`) y esperan hasta 15 s; sin respuesta = no. `--yes` o `MPVD_MCP_AUTOCONFIRM=1`
  las omiten (útil en scripts propios, no recomendado para un asistente).

## Tools
| Tool | Hace | Confirma |
|---|---|---|
| `status` | archivo/URL, título, posición, duración, pausa, pistas de subtítulos, sesiones, tareas de subtítulos IA | no |
| `play {target, mode}` | `loadfile` (replace/append/append-play) | sí, si ya hay algo |
| `pause` / `resume` | `pause` | no |
| `seek {seconds, mode}` | relativo o absoluto | sí |
| `search_dialogue {query, path?, limit}` | busca en los segmentos Whisper (`asr.search`); si no hay transcripción la lanza | no |
| `list_channels {query, limit, kind}` | `iptv.search` (TDT, iptv-org, Radio Browser, listas propias) | no |
| `play_channel {id}` | `mu-iptv-play` con cabeceras correctas | sí |
| `download {url, preset}` | `ytdl.download` (presets de docs/YTDLP.md) | sí |
| `add_note {text, time_pos?}` | Markdown en `<data_dir>/notas/<clave>.md` con enlace `mpv://seek?t=` | no |
| `subtitles_ai {action, language}` | start/stop/status de mu-subs | no |

## Resources
- `mpv://transcript/<id de tarea asr>` → SRT de la transcripción (application/x-subrip).
- `mpv://notes/<clave>` → notas Markdown.

## Métodos JSON-RPC nuevos en mpvd (usables también desde `python -m mpvd call`)
`session.get/set/command/confirm` (control de una sesión de mpv), `notes.add/list/read`, `asr.search`.

## Prueba manual
```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"sh","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"status","arguments":{}}}' | .venv/bin/python -m mpvd mcp
```
