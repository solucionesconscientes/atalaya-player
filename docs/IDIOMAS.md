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
`t('Abrir o descargar')` busca esa cadena en el catálogo del idioma activo. No hay claves tipo `menu.open.title`.

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
en Windows, donde el entorno no lleva nada).

Una preferencia en *Preferencias → Idioma* lo fuerza (automático / castellano / English / Français), porque alguien
con el sistema en inglés puede querer el reproductor en castellano. Se recuerda en mu-prefs.

### 3. Los catálogos: `locales/en.json` y `locales/fr.json`
JSON plano `{"cadena en castellano": "translation"}`, leído por `mu/i18n.lua` y por `mpvd/i18n.py`. No hay `es.json`.
JSON porque los dos lados ya lo parsean (precedente: `brand.json`) y porque se puede revisar y corregir a mano sin
herramientas.

### 4. Las frases compuestas se arreglan al extraerlas
Hay cadenas construidas con `..` y con `string.format`. Una frase partida en trozos no se puede traducir: el orden
de las palabras cambia entre idiomas. Así que al extraer, lo que esté concatenado pasa a una sola cadena con
posiciones: `t('%d archivos en la lista'):format(n)`. Es la parte que no se puede automatizar y la que lleva el
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
