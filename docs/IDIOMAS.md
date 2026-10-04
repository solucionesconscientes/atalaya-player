# Idiomas (H49) · castellano, inglés y francés

Regla de Ser: **si el sistema está en castellano o en francés, la aplicación en ese idioma; en inglés o en cualquier
otro, en inglés.**

## Lo que ya estaba, medido antes de diseñar

- **1.151 cadenas visibles en los scripts Lua** (817 en `title`/`hint`/`label`/`footnote`, 334 en avisos del OSD),
  **~520 mensajes en castellano en mpvd** y unas **56 en el JavaScript de las páginas servidas**. En total ~1.800, que
  traducidas a dos idiomas son ~3.600. No es un cambio pequeño: va por etapas, con `tools/check.sh` en verde en cada una.
- **uosc ya está traducido**: trae `intl/es.json`, `intl/fr.json` y más, y el idioma se elige con su opción
  `languages` (hoy `es,slang,en` en `script-opts/uosc.conf`). Sus menús y sus mensajes no hay que traducirlos: basta
  con decirle el idioma. Eso se aprovecha, no se duplica.
- **mpv no expone el idioma del sistema**: no hay ninguna propiedad. La fuente es el entorno (`LC_ALL`,
  `LC_MESSAGES`, `LANG`, en ese orden, que es el de POSIX). En Windows no suelen existir, y ahí Python sí sabe
  (`locale.getlocale()`).

## Decisiones

### 1. La cadena en castellano **es** la clave
`tr('Abrir o descargar')` busca esa cadena en el catálogo del idioma activo. No hay claves tipo `menu.open.title`.

Por qué, que es la decisión que hace viable migrar 1.800 cadenas:
- El código sigue **legible**: se ve el texto real donde está, no un identificador que hay que ir a buscar.
- El castellano **no necesita catálogo**: es la identidad, cero ficheros que mantener sincronizados.
- Si alguien cambia el texto castellano y olvida el catálogo, la cadena **cae al castellano**, que es la clave. El
  peor caso es «se ve en español», nunca una clave cruda, un hueco en blanco ni un error.
- La extracción es mecánica: lo que hay entre comillas ya es el msgid.

### 2. Un solo sitio decide el idioma: `bin/mpv-uos`
El lanzador mira el entorno, resuelve `es` / `fr` / `en` y lo pasa a los dos lados:
- a uosc, con `--script-opts-append=uosc-languages=<lang>,slang,en`, para que sus propias traducciones entren solas;
- a nuestros scripts, con `--script-opts-append=mu-core-lang=<lang>`.

Así el idioma está decidido **antes** de que cargue el primer script, y no hay un momento en que la pantalla de
inicio salga en un idioma y cambie al siguiente. mpvd lo resuelve por su cuenta con `locale` (y así también acierta
en Windows, donde el entorno no lleva nada). El lanzador de Windows (`bin/mpv-uos.ps1`) hace lo mismo y además mira
`Get-UICulture`; hay un test que comprueba que los dos construyen la misma orden.

**Corrección importante, aprendida al implementarlo:** el diseño decía que mu-core resolvería el idioma y los demás
scripts lo leerían de él. **No funciona**, porque cada script de mpv corre en su **propio estado Lua**: `mu.i18n` es
una instancia distinta en cada uno y lo que fije mu-core no llega a nadie. Así que es el **módulo** el que lee la
opción del lanzador, de `options/script-opts`, en cada script donde se carga. Se vio con el menú saliendo en
castellano con el reproductor en inglés, y es la clase de cosa que un test de un solo script no habría cogido.

Una preferencia en *Preferencias → Idioma* lo fuerza (automático / castellano / English / Français), porque alguien
con el sistema en inglés puede querer el reproductor en castellano. Se recuerda en mu-prefs.

### 3. Los catálogos: `locales/en.json` y `locales/fr.json`
JSON plano `{"cadena en castellano": "translation"}`, leído por `mu/i18n.lua` y por `mpvd/i18n.py`. No hay `es.json`.
JSON porque los dos lados ya lo parsean (precedente: `brand.json`) y porque se puede revisar y corregir a mano sin
herramientas.

### 3.b La preferencia y el extractor, con sus trampas
- **La preferencia** (*Preferencias → Idioma*) la guarda mu-prefs en `prefs.json`, que es un fichero plano, así que
  **el lanzador puede leerla** y hacerla ganar al entorno. Se aplica **al reiniciar**: las cadenas se resuelven al
  cargar los scripts, y fingir que cambian en caliente sería mentira.
- **El extractor** (`tools/i18n_extract.py`) solo envuelve posiciones conocidas, y recoge además los dos primeros
  argumentos de los constructores de filas de mu-menu (`cmd`, `bind`, `sub`, `child`, `toggle`), que **traducen por
  dentro**: así las 54 llamadas siguen leyéndose en castellano y hay un solo sitio que puede equivocarse.
- **Trampa del filtro de teclas**: un `hint` es muchas veces el nombre de una tecla (`ctrl+b`, `alt+R · alt+I`, `?`),
  y eso no es texto. El primer filtro descartaba «lo corto», y se tragó **«Grabar» y «Salir»**. La regla correcta:
  es una tecla si lleva modificador, si es un solo carácter o si es uno de los nombres de tecla de mpv. Ser corto no
  basta.
- **Trampa del título como clave**: el modo sencillo elegía sus categorías por el **título**, que es justo lo que
  cambia de idioma. Cada categoría tiene ahora un `id` estable. Antes de traducir un script conviene buscar
  comparaciones y accesos por título.

### 4. Las frases compuestas se arreglan al extraerlas
Hay cadenas construidas con `..` y con `string.format`. Una frase partida en trozos no se puede traducir: el orden
de las palabras cambia entre idiomas. Así que al extraer, lo que esté concatenado pasa a una sola cadena con
posiciones: `tr('%d archivos en la lista'):format(n)`. Es la parte que no se puede automatizar y la que lleva el
tiempo.

### 5. Las páginas servidas siguen al **navegador del invitado**, no al sistema del anfitrión
La sala, el mando del móvil y el panel de descargas los abre otra persona, quizá en otro idioma. Se decide por su
`Accept-Language`, con el mismo criterio (es/fr, y si no en inglés). Es lo contrario de lo que haría el camino fácil
—usar el idioma del anfitrión— y es lo correcto.

### 6. Qué NO se traduce
- Los nombres propios y la marca.
- Los documentos del repositorio (`docs/`), los ADR, los commits y los comentarios del código: la regla del proyecto
  sigue igual (código y comentarios en inglés, documentación y commits en castellano). Lo que se traduce es lo que
  **ve quien usa el programa**, no lo que leemos nosotros.
- El texto de los medios de prueba: `tools/make_test_media.sh` genera voz que lo dice, y cambiarlo rompería los
  tests de reconocimiento.

## Etapas
1. **Maquinaria**: `locales/`, `mu/i18n.lua`, `mpvd/i18n.py`, la detección en `bin/mpv-uos`, la preferencia y los
   tests (incluido uno que compruebe que una cadena sin traducir cae al castellano y no deja un hueco).
2. **Lo primero que se ve**: `mu-core`, `mu-menu` (menú, paleta, ayuda, pantalla de inicio) y `mu-modes`.
3. **Los módulos grandes**: ytdl, iptv, subs, record, share, library, music.
4. **Los mensajes de mpvd**, que salen en el OSD.
5. **Las páginas servidas** (sala, mando, descargas).
6. **Repaso de las traducciones**, el francés con ojo: lo escribo yo y conviene que alguien lo lea.

## Cómo acabó (H49, iteración 9)
- **1.661 cadenas** por idioma, inglés y francés completos, sin ninguna vacía (un valor vacío cae al castellano,
  así que un hueco no se ve; lo vigila `tests/test_i18n.py`).
- Tres extractores, uno por lenguaje, y los tres son la fuente del test que compara código y catálogo:
  `tools/i18n_extract.py` (Lua), `tools/i18n_extract_py.py` (los mensajes de mpvd) y `tools/i18n_extract_web.py`
  (las páginas servidas: los `t('…')` de sus `.js` y el texto de sus `.html`).
- Las páginas servidas no reciben el catálogo entero, sino las cadenas que esa página usa, en el idioma que pide el
  navegador del invitado, servidas en `/i18n.js` (la sala, en `/static/i18n.js`) delante de `mpvd/i18n_page.js`
  (ADR-107). El texto que ya viene escrito en el HTML se traduce en el propio navegador. Un `<script>` en línea no
  vale: la sala se sirve con `script-src 'self'` y el navegador lo bloquea sin avisar.
- Lo que no se traduce sigue siendo lo de la lista de arriba, más dos cosas que se decidieron al repasar:
  - Los nombres de país de `mpvd/iptv/labels.py` (unos 240): son datos, vienen en castellano de la lista de
    canales y traducirlos es un trabajo de datos, no de interfaz. Anotado en el BACKLOG (H49/G8).
  - Las descripciones de las herramientas MCP (`mpvd/mcp.py`): las lee un modelo, no una persona.
- Lo que vino en G8 (ADR-113): una **tabla de datos** (formatos, modelos, nombres de tareas) se traduce **donde se
  sirve**, nunca donde se define —ahí se evaluaría al importar el módulo y se quedaría fijada—, y el extractor la
  recoge por el nombre del campo (`label`, `hint`, `title`, `description`, `note`). Y un mensaje que nace dentro de
  una petición HTTP habla el idioma de **quien la hace**: cada petición lo fija en una variable de contexto.
