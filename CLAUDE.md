# MPV-UOS — reglas del proyecto (léelas SIEMPRE antes de actuar)

## Qué es
Reproductor multiplataforma "del futuro" sobre mpv (≥0.41) + uosc (≥5.13): scripts Lua finos dentro de mpv
+ un daemon companion en Python ("mpvd") que hace lo pesado (IA, red, índices, descargas) y habla con mpv por JSON IPC.
Visión: docs/VISION.md · Plan: BACKLOG.md · Estado: PROGRESS.md · Bloqueos: NEEDS_HUMAN.md · Decisiones: docs/DECISIONS.md

## Modo nocturno autónomo
- Nadie puede responder preguntas. No preguntes nunca: decide, documenta la decisión en docs/DECISIONS.md (ADR corto) y sigue.
- Trabaja hito a hito en el orden de BACKLOG.md. Un hito se marca [x] solo cuando sus criterios de aceptación pasan con tests automáticos.
- Si algo se bloquea >45 min o necesita a un humano (sudo, credenciales, hardware), anótalo en NEEDS_HUMAN.md con el comando exacto
  que tendría que ejecutar Ser, marca la tarea [~] y pasa a la siguiente.
- Al cerrar cada hito: tools/check.sh en verde → commit → actualizar BACKLOG.md y PROGRESS.md (qué se hizo, cómo probarlo a mano, qué falta).
- Antes de agotar contexto, deja en PROGRESS.md un "SIGUIENTE PASO" preciso para la próxima iteración.
- Usa subagentes para investigación acotada (manual de mpv instalado, wiki Menu API de uosc, `yt-dlp --help`, formato real de las M3U)
  y no llenes tu contexto. Verifica siempre contra la fuente real antes de asumir sintaxis u opciones.

## Límites obligatorios
- Todo dentro de /home/pc/Documentos/PROJECTES/MPV-UOS/. Fuera, solo lectura:
  ~/proyectos/live-captions-linux (reutilizar su whisper.cpp) y /home/pc/Documentos/PROJECTES/CODE-NOTION/ (convención de Notion).
- Nada de sudo ni paquetes del sistema. Python con uv (.venv del proyecto); binarios y modelos en vendor/ (ignorado por git).
- NO tocar ~/.config/mpv. mpv se ejecuta SIEMPRE vía bin/mpv-uos con --config-dir=<proyecto>/mpv-config.
- Git: trabaja en la rama actual nocturno/<fecha>; commits pequeños en español; nunca push, force, rebase de historia publicada ni merge a main.
- Solo se puede borrar dentro de vendor/, .cache/, tmp/ del proyecto.
- Procesos: mata SOLO los PID que hayas lanzado tú en esta sesión (guárdalos al arrancarlos). Prohibido matar por patrón
  (`pkill -f`, `killall`, `ps | grep | xargs kill`): coincide con el propio runner, con Claude y con programas de Ser
  (navegadores, reproductores). Nunca toques procesos ajenos al proyecto. Para navegadores de prueba usa perfiles en tmp/
  y ciérralos por PID.
- Tests sin ventanas: mpv con --vo=null --ao=null (o --no-video) --idle=yes --input-ipc-server=<socket en tmp/>.
- Sin secretos en el repo. Servicios de nube opcionales y DESACTIVADOS por defecto (local-first).
- Hardware objetivo mínimo: portátil de 4 núcleos sin GPU dedicada. Detecta hardware (nproc, RAM, vulkaninfo/vainfo si existen) y elige modelos acordes.

## Stack y convenciones
- mpv del sistema: comprueba `mpv --version` y valida cada comando/opción contra el manual de ESA versión (no inventes opciones).
- uosc: última release estable vendorizada en mpv-config/scripts/uosc (versión fijada en vendor.lock). Integración SOLO por su API pública
  (script-message open-menu/update-menu/close-menu, set-button, etc.). No edites uosc; si hiciera falta, parche documentado en patches/.
- Scripts propios Lua (LuaJIT/5.1), prefijo mu- (mu-core, mu-iptv, mu-ytdl, mu-subs, mu-audio…). Nunca bloquear el hilo de Lua:
  mp.command_native_async/subprocess asíncrono; lo pesado va a mpvd. OSD/overlays a ≤10–20 Hz.
- mpvd: Python 3.12+, asyncio, dependencias mínimas. JSON-RPC 2.0 versionado sobre socket Unix (diseño preparado para named pipe en Windows).
  Método `capabilities` obligatorio. Caché SQLite + blobs por (hash_archivo, artefacto, modelo, versión, parámetros); en desarrollo en .cache/,
  en producción rutas XDG (platformdirs).
- Tests: pytest (unit + integración con mpv headless); marca @pytest.mark.network lo que dependa de internet. Lint Lua con luacheck si existe,
  si no `luajit -bl` para sintaxis. tools/check.sh lo ejecuta todo y debe pasar antes de cada commit.
- Idiomas: código y comentarios en inglés; documentación, menús de uosc y commits en español.
- Multiplataforma: sin rutas hardcodeadas; documenta en docs/PLATAFORMAS.md lo no probado en Windows/macOS.

## Notion (opcional)
Si en la sesión hay MCP de Notion y lo permite el modo de permisos, registra/actualiza el proyecto "MPV-UOS" siguiendo la convención de
/home/pc/Documentos/PROJECTES/CODE-NOTION/ (léela primero; busca antes de crear para no duplicar). Si no es posible, anótalo en NEEDS_HUMAN.md.

## Notion
Proyecto Notion: slug=mpv-uos
