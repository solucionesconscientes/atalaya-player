# Capítulos vía IA externa — Atalaya Player

## Qué hace
Atalaya NO incluye ningún modelo para esto. El usuario obtiene la transcripción (SRT junto al vídeo), Atalaya la prepara
junto a un prompt y la copia al portapapeles; el usuario la pasa a la IA que quiera y pega de vuelta la respuesta;
Atalaya la convierte en capítulos de mpv (uosc ya los dibuja en la barra). Los subtítulos NO se tocan: mpv carga solo el
SRT con el mismo nombre.

## Arquitectura
- 100 % Lua, sin dependencias externas (el reproductor es multiplataforma). Compatible con LuaJIT/Lua 5.1: nada de
  operadores ni librerías de 5.3+.
- Módulos puros (sin depender de mp) para poder probarlos aparte: srt (parseo y compactado), chapters (parseo tolerante
  de la respuesta, ajuste, validación, formato).
- Capa fina de mpv: atajos, portapapeles, OSD, chapter-list, archivo lateral.

## Flujo
Cuatro pasos, los mismos que cuenta la pantalla de ayuda dentro del reproductor (menú → capítulos → *Crear capítulos
con una IA…* → *Cómo funciona*):

1. **Copiar.** Si el vídeo trae subtítulos (pista de texto o `.srt` al lado), «Copiar subtítulos y el prompt»
   (`ctrl+alt+c` → binding `chapters-copy-all`) deja en el portapapeles el prompt, **una línea en blanco** y la
   transcripción compacta, en ese orden: el prompt ya acaba en la línea «TRANSCRIPCIÓN:». OSD:
   «Prompt y transcripción copiados: N líneas (~X mil tokens)».
   Si no los trae, la misma tecla copia **solo el prompt** (`chapters-copy`: contenido de `prompt-ia.txt` +
   «DURACIÓN TOTAL: h:mm:ss» + «TRANSCRIPCIÓN:») y el OSD es «Prompt copiado».
2. **Copiar por separado** (pantalla de ayuda): «Copiar solo la transcripción» (`chapters-copy-transcript`) copia solo
   las líneas «[mm:ss] texto», con OSD «Transcripción copiada: N líneas (~X mil tokens)»; «Copiar solo el prompt» es
   `chapters-copy`. Y «Abrir riverside.com/transcription» (`chapters-open-riverside`) abre esa página en el navegador
   del sistema (xdg-open / start / open), con OSD «Abriendo riverside.com/transcription». Whisper se queda al final,
   en voz baja: «Conseguir la transcripción aquí (tarda)» (`chapters-transcript`, mu-subs).
3. **Pegar capítulos** (`ctrl+alt+p`): lee el portapapeles, parsea, valida, ajusta, guarda nombre.capitulos.txt
   (formato "MM:SS Título", editable a mano; si ya existe, copia de seguridad .bak) y asigna chapter-list. OSD:
   «N capítulos · M puntos clave aplicados y guardados junto al vídeo» cuando la respuesta trae el segundo bloque, y
   «N capítulos aplicados y guardados junto al vídeo» cuando no.
4. Al abrir un archivo, si existe nombre.capitulos.txt se cargan al instante.

## Transcripción compacta
Une bloques del SRT en líneas de ~10-15 s: "[mm:ss] texto" ("[h:mm:ss]" si el vídeo dura >= 1 h, en todas las líneas).
Quita etiquetas HTML, colapsa espacios, elimina líneas vacías y duplicadas consecutivas y las frases típicas de
alucinación de Whisper («Gracias por ver el vídeo», «Suscríbete», «Subtítulos por…»).

## Parseo de la respuesta de la IA (tolerante)
El prompt pide **dos bloques en una sola respuesta**: BLOQUE 1 — CAPÍTULOS, una línea por capítulo, en un bloque de
código con formato `MM:SS Título`; BLOQUE 2 — PUNTOS CLAVE, de 8 a 12 viñetas `- MM:SS — una frase`.

`parse_answer` lee los dos y `parse_reply` devuelve solo los capítulos: acepta con o sin bloque de código, con
viñetas/numeración/negritas/corchetes, separadores " ", "-", "–", "—", ":", y tiempos mm:ss o h:mm:ss; limpia el
título (markdown, comillas, signos finales). Ignora las líneas de valla (```) y **para en la primera línea que no es un
capítulo después de haber leído al menos uno**: así el encabezado del segundo bloque cierra el primero y las viñetas
—que también empiezan por un tiempo— no se cuelan como capítulos. Las listas planas sin encabezados siguen valiendo
igual. `parse_answer` cuenta además las líneas con tiempo que vienen detrás del corte (`points`), que es lo que dice el
OSD al pegar («N capítulos · M puntos clave»).

## Validación y ajuste
- Descartar tiempos > duración y duplicados; ordenar; separación mínima 45 s (se conserva el anterior).
- Si falta el 00:00, insertar capítulo «Inicio».
- Ajuste: cada tiempo se mueve al inicio de bloque del SRT más cercano si está a <= 15 s.
- Control de calidad: si más de la mitad de los tiempos no está a <= 15 s de ningún inicio de bloque, avisar «los
  tiempos no parecen de esta transcripción» y pedir confirmar antes de aplicar.
- Sin SRT: se puede pegar igualmente, sin ajuste ni control.

## Reglas
- Solo archivos locales con duración conocida; nada en IPTV/radio en directo.
- Si mpv ya trae capítulos propios y respect_existing=yes, no se pisan (aviso al pegar).
- Prompt en prompt-ia.txt editable (con el contenido recomendado en este mismo directorio).
- Portapapeles: usar la propiedad de mpv si existe en la versión instalada (comprobar con mpv --version y probándola);
  si no, subprocess con wl-copy/wl-paste, xclip, pbcopy/pbpaste o PowerShell según el sistema. Errores claros si falta
  la herramienta.

## Fuera de alcance
Instalar o ejecutar modelos locales; llamadas a APIs de IA; sincronizar subtítulos; panel de resumen; cambiar uosc o su
estructura; dependencias nuevas.

## Hecho cuando
Flujo completo probado con un vídeo real y una IA real: copiar, pegar, capítulos visibles en la barra de uosc y
recargados al reabrir; tests de los módulos puros pasando.

---

# Adaptaciones a este repo (leer antes de implementar)

La ficha de arriba viene de otro flujo. En este repositorio hay reglas escritas en `CLAUDE.md` y piezas ya hechas que
mandan sobre lo que diga la ficha. Estas son las diferencias que hay que respetar:

1. **Nombre del script: `mu-chapters`.** Regla del repo: scripts propios con prefijo `mu-` y nombre en inglés
   (`mpv-config/scripts/mu-chapters/main.lua`); el menú y los avisos, en español. El nombre del script-message es
   `mu_chapters` (guion bajo: es el nombre del fichero sin `.lua`).
2. **Todo texto visible pasa por `mu.i18n`** (`local tr = require('mu.i18n').t`) y las cadenas nuevas van a
   `locales/en.json` y `locales/fr.json`. Lo exige `tests/test_i18n.py`: si falta una cadena, la batería falla.
3. **Portapapeles: solo `clipboard/text` de mpv** (0.41 ya lo tiene). El proyecto ya lo usa en `mu-ytdl` (pegar URL) y en
   `mu-core` (copiar enlaces). **No** añadas `wl-copy`/`xclip`/`pbcopy`/PowerShell propios: sería una segunda vía que se
   comporta distinto en Windows, justo lo contrario de lo que busca el proyecto. Si el backend no está, se dice en
   pantalla (mpv devuelve vacío). Hay precedente de cómo probarlo: el script-opt `mu-ytdl-clipboard_text` fija el
   contenido del portapapeles en los tests.
4. **Módulos puros: sí, pero dónde y cómo se prueban.** El módulo va en `mpv-config/script-modules/mu/chapters.lua`
   (junto a `rpc`, `uosc`, `nav`, `prefs`, `i18n`). Los tests **no** usan un intérprete Lua suelto: en esta máquina no
   hay `luajit` ni `lua5.1` (`tools/check.sh` lo avisa), y el proyecto prueba siempre con **pytest + mpv sin ventana**
   (`tests/test_mu_*.py`, con `--vo=null --ao=null`, IPC en `tmp/`). Para llamar a las funciones puras desde el test,
   expón un script-message de prueba y/o un script-opt de prueba; el flujo completo, con teclas de verdad
   (`keypress`), como en `tests/test_mu_goto.py`.
5. **El fichero `nombre.capitulos.txt` junto al vídeo es correcto aquí**: `mpvd/subs/save.py` ya escribe el SRT junto al
   vídeo cuando el usuario lo pide («Guardar subtítulos (SRT)»). Regla: ese fichero **solo** se escribe cuando el
   usuario pega capítulos (nunca solo por abrir o reproducir), y con `.bak` si ya existía. En IPTV/radio, nada.
6. **De dónde sale el SRT.** El repo ya transcribe en local con whisper (`mu-subs`, `alt+i`) y tiene «Guardar
   subtítulos (SRT)» (`alt+S`), que escribe `nombre.srt` junto al vídeo. El flujo de esta función lee **ese** SRT. Si
   el vídeo no tiene SRT al lado, se dice claro («no hay transcripción junto al vídeo; guárdala con alt+S») y no se
   inventa nada.
7. **Antes de cada commit: `tools/check.sh` en verde** (lint Lua con luacheck + pytest sin red + los `@network`).
   Commits pequeños en español, en una rama propia; nada de push a `main`.
8. **mpv ≥ 0.41 es requisito del proyecto** para escribir `chapter-list` (mu-core ya avisa si el mpv es más viejo), así
   que no hace falta comprobación nueva.
9. **Delegación:** para implementar fases bien definidas como estas, la regla de este repo es la skill `delegar-opencode`
   (se planifica y se verifica aquí; el código lo escribe OpenCode). El modelo que diga cada brief es orientativo.
