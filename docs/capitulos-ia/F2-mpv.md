# F2 — Integración en mpv

> Antes de empezar: lee `CLAUDE.md` (manda), **`docs/capitulos-ia/FICHA.md` entero** con su sección «Adaptaciones a este
> repo», y los módulos de F1.

Modelo previsto: sonnet (Flash). Motivo: alcance claro; el contrato con los módulos ya existe.

## Contexto
Repo existente (Atalaya Player = mpv + uosc + `mpvd`). Módulos puros ya hechos en F1. Rama `feat/capitulos-ia`.

## Objetivo
Script `mpv-config/scripts/mu-chapters/main.lua` (nombre del script-message: `mu_chapters`) con los dos atajos del flujo,
la carga automática de `nombre.capitulos.txt` al abrir un archivo y su entrada en el menú de uosc.

## Alcance
Hacer:
- Capa fina de mpv (`mu.rpc`, `mu.uosc`, `mu.nav`, `mu.prefs`, `mu.i18n` como los demás `mu-*`), con script-opts
  (`mpv-config/script-opts/mu-chapters.conf`) y script-messages.
- Dos atajos por defecto que **no choquen** con `mpv-config/input.conf` ni con los de uosc: compruébalo y dilo en el
  resumen. Y una entrada «Capítulos» en el menú principal (`mu-menu`), porque en este repo todo se puede hacer sin
  recordar teclas.
- Portapapeles: **solo `clipboard/text` de mpv** (leer y escribir), como en `mu-ytdl` y `mu-core`. Copiar el prompt
  para la IA; leer la respuesta pegada. Nada de `wl-copy`/`xclip`/PowerShell propios. Para los tests, un script-opt de
  prueba al estilo de `mu-ytdl-clipboard_text`.
- OSD con mensajes claros (y traducidos) para: no hay SRT junto al vídeo, copiado con cuántas líneas, portapapeles
  vacío, formato no reconocido, tiempos sospechosos (pedir confirmación), capítulos aplicados, y capítulos propios que
  no se pisan.
- Guardar y cargar `nombre.capitulos.txt` junto al vídeo (con `.bak` si ya existía), `respect_existing`, y no hacer nada
  en IPTV/radio en directo.
- Tests de integración en `tests/test_mu_chapters.py` con mpv sin ventana, siguiendo `tests/test_mu_goto.py`.

NO hacer: modelos, APIs, panel de resumen, tocar uosc, duplicar el parseo (eso es F1).

## Criterios de aceptación
- [ ] Con un vídeo real: el atajo A copia, lo pego en una IA, copio su respuesta, el atajo B pone las marcas en la barra
      de uosc.
- [ ] Reabrir el vídeo: capítulos al instante desde el archivo.
- [ ] Sin SRT, portapapeles vacío o respuesta basura: mensaje claro y mpv sigue normal (y `h.script_errors() == []`).
- [ ] En IPTV/radio el script no hace nada ni da errores en el log.
- [ ] `tools/check.sh` en verde.

## Forma de trabajar
Rama `feat/capitulos-ia`. Commits pequeños en español.

## Al terminar
Resumen llano, comandos de prueba en un solo bloque y para a esperar mi revisión.
