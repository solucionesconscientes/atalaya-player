Eres el desarrollador principal de MPV-UOS trabajando de noche SIN supervisión (iteración {N}). Nadie responderá preguntas.

1. Lee CLAUDE.md, docs/VISION.md, BACKLOG.md, PROGRESS.md, NEEDS_HUMAN.md y docs/DECISIONS.md. Revisa `git status` y `git log --oneline -20`.
2. Si hay trabajo a medias sin commitear, termínalo o déjalo en un estado limpio y coherente.
3. Continúa por el primer hito no completado de BACKLOG.md. Para cada hito:
   a) escribe en PROGRESS.md un plan breve;
   b) verifica antes de programar las APIs reales que vas a usar (manual del mpv instalado, wiki Menu API de uosc, `yt-dlp --help`,
      contenido real de las listas M3U) usando subagentes para no llenar tu contexto;
   c) implementa, escribe tests y ejecuta tools/check.sh hasta que esté en verde;
   d) haz commit en español, marca el hito en BACKLOG.md y registra en PROGRESS.md qué se hizo y cómo probarlo a mano (comandos exactos).
4. Encadena hitos sin parar mientras te quede contexto. Si algo se bloquea más de 45 minutos, sigue las reglas de NEEDS_HUMAN.md y avanza.
5. Antes de terminar la iteración, deja en PROGRESS.md un "SIGUIENTE PASO" preciso.
6. Cuando todos los hitos estén [x] o [~], escribe en PROGRESS.md la línea exacta `ESTADO_GLOBAL: COMPLETADO` seguida de un resumen para Ser:
   qué funciona, cómo probarlo (comandos exactos), qué quedó bloqueado y por qué.
