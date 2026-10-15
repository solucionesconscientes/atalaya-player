# F1 — Módulos Lua puros y tests

> Antes de empezar: lee **`docs/capitulos-ia/FICHA.md` entero**, incluida la sección «Adaptaciones a este repo».
> Manda sobre cualquier suposición: rutas, pruebas, i18n y portapapeles son los de aquí, no los de un repo genérico.

Modelo previsto: opus (Pro). Motivo: parseo tolerante y ajuste de tiempos; los fallos pasan desapercibidos.
En este repo, además, la regla es delegar la escritura de código con la skill `delegar-opencode` (se planifica y se
verifica aquí).

## Contexto
Repo existente (Atalaya Player = mpv + uosc + `mpvd`). Lee `CLAUDE.md` (sus reglas mandan) y la ficha. Haz
reconocimiento de `mpv-config/script-modules/mu/` y de `tests/test_mu_*.py` e imita esas convenciones. Rama propia:
`feat/capitulos-ia`, desde la rama actual (`ci/windows-instalador`).

## Objetivo
Módulos Lua sin dependencia de `mp` con: parseo de SRT; compactado a líneas "[mm:ss] texto" con filtros; parseo tolerante
de la respuesta de la IA; ajuste a inicios de bloque; validación y control de calidad; formateo a "MM:SS Título".

## Alcance
Hacer:
- Módulo en `mpv-config/script-modules/mu/chapters.lua` (y, si el parseo de SRT merece su propio sitio,
  `mu/srt.lua`). **Antes de escribirlo, mira si ya hay algo que parsee SRT en `script-modules/mu/` y reúsalo**: no
  dupliques.
- Todo lo descrito en la ficha para esos módulos, compatible con LuaJIT/Lua 5.1 (nada de `utf8.*`, `//` ni novedades de
  5.3+).
- Tests en `tests/test_mu_chapters.py` con el patrón del repo: **pytest + mpv sin ventana** (`start_mpv` de
  `tests/conftest.py`, con `--vo=null --ao=null --idle=yes` e IPC en `tmp/`). Para llamar a las funciones puras, expón
  un script-message de prueba; el flujo completo, con teclas de verdad (`keypress`), como en `tests/test_mu_goto.py`.
  Casos: SRT con BOM/CRLF/etiquetas/tiempos con coma; respuestas de IA en todos los formatos de la ficha (con y sin
  bloque de código, viñetas, negritas, corchetes, h:mm:ss); tiempos fuera de rango, duplicados, desordenados, sin
  00:00; ajuste a ±15 s; control de calidad.
- Fixtures: usa un SRT real de `tests/fixtures/` (si no hay ninguno, crea uno pequeño y di de dónde salió).

NO hacer: capa de mpv, atajos, menús, portapapeles, Python, dependencias nuevas, panel de resumen.

## Criterios de aceptación
- [ ] `tools/check.sh` en verde (lint Lua con luacheck y `tests/test_mu_chapters.py` completo).
- [ ] Ejemplo con un SRT real: el compactado queda legible y el recuento aproximado de tokens se calcula.
- [ ] Ninguna función usa `utf8.*`, `//` ni otras novedades de Lua 5.3+.
- [ ] Los textos que vea el usuario están envueltos en `tr()` y sus cadenas añadidas a `locales/en.json` y
      `locales/fr.json` (`tests/test_i18n.py` lo exige).

## Forma de trabajar
Presenta primero un plan corto (archivos, API de cada módulo) y espera mi OK antes de editar. Commits pequeños en
español.

## Al terminar
Resumen llano con comandos de prueba en un solo bloque y para a esperar mi revisión. Si algo bloquea, 2 alternativas con
su coste.
