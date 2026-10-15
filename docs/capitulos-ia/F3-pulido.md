# F3 — Pulido

> Antes de empezar: lee **`docs/capitulos-ia/FICHA.md`** (con «Adaptaciones a este repo») y el estado de F1 y F2.

Modelo previsto: sonnet (Flash). Motivo: tareas pequeñas y mecánicas.

## Contexto
Repo existente (Atalaya Player = mpv + uosc + `mpvd`). F1 y F2 hechas.

## Objetivo
Dejarlo cómodo y documentado como el resto del proyecto.

## Alcance
Hacer:
- `prompt-ia.txt` se instala junto al módulo (`mpv-config/script-modules/mu/` o donde decida F2) y se lee de ahí
  (editable); si falta, usar un prompt por defecto embebido. El original de referencia queda en `docs/capitulos-ia/`.
- Opciones comentadas en `mpv-config/script-opts/mu-chapters.conf`: atajos, separación mínima, tolerancia de ajuste,
  `respect_existing`, longitud de línea compacta.
- Atajo (u opción de menú) para borrar los capítulos de ese vídeo.
- **Documentación donde el proyecto la tiene** (esto es lo que hay que hacer, no un README suelto):
  - `docs/ATAJOS.md`: las teclas nuevas.
  - `docs/USO.md`: el flujo en 4 pasos, de dónde sale la transcripción (`alt+S` la guarda junto al vídeo), el prompt que
    se copia, qué IAs se han probado y qué hacer con transcripciones muy largas (partir el vídeo y pegar capítulos por
    separado **no** está soportado: documentarlo como límite).
  - `PROGRESS.md` y `BACKLOG.md`: la entrada del hito, como en los demás (con el «probar a mano»).
  - `docs/DECISIONS.md`: solo si se tomó alguna decisión que no estuviera escrita (p. ej. las teclas elegidas).
NO hacer: nuevas funciones, panel de resumen, dependencias nuevas.

## Criterios de aceptación
- [ ] Desde cero, siguiendo solo `docs/USO.md`, se instala y funciona.
- [ ] Editar `prompt-ia.txt` cambia lo que se copia.
- [ ] `tools/check.sh` en verde.

## Al terminar
Resumen llano con comandos de prueba y espera revisión. No hagas merge ni push.
