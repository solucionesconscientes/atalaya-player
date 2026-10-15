# F4 — Transcribir y copiar para la IA

> Antes de empezar: lee `CLAUDE.md` (manda), **`docs/capitulos-ia/FICHA.md` entera** con su sección «Adaptaciones a este
> repo», y el estado de F1, F2 y F3.

Modelo previsto: sonnet (Flash). Motivo: alcance claro y todo apoyado en piezas que ya existen (`mu-subs` transcribe y
cachea, `subs.extract` extrae pistas, `mu-chapters` ya hace el flujo copiar/pegar).

## Contexto

El flujo actual da por hecho que el usuario ya tiene el SRT junto al vídeo y que sabe qué teclas pulsar. El usuario
objetivo **no sabe**: no debe tener que entender qué es whisper, ni el SRT, ni el portapapeles, ni elegir un modelo.
Decisión de Ser (15-oct-2026): **una sola copia** (prompt + transcripción juntos) y el usuario abre la IA que quiera y
pega. **No** se abre el navegador automáticamente: queda como opción futura, no entra en esta fase.

## Objetivo

Que alguien que no sabe nada pueda, desde el propio reproductor: conseguir la transcripción sin elegir nada, ver que se
está haciendo, enterarse de que terminó, copiarlo todo de una vez junto con el prompt, y volver a pegar la respuesta
para tener los capítulos.

## Alcance

Hacer:

1. **Un solo botón en el menú** (entrada en el menú de uosc, como el resto del repo: aquí todo se hace sin recordar
   teclas) — «Capítulos: transcribir» — que:
   - si el vídeo **ya trae una pista de subtítulos de texto**, la extrae (`subs.extract`) y la usa, diciéndolo;
   - si no hay nada que extraer (o la pista es de imagen, que no se puede convertir a texto), transcribe con whisper
     por la cadena que ya existe en `mu-subs`, **sin preguntar modelo**: el que elija el tier de hardware;
   - en IPTV/radio en directo no hace nada.
2. **Indicador claro de proceso**: mientras trabaja, un OSD visible con el progreso real del trabajo («Transcribiendo…
   37 %») y, si se puede estimar, el tiempo que queda («listos en ~22 min»). Nada de barras falsas.
3. **Aviso al terminar**: mensaje inequívoco («Transcripción lista: N líneas, M caracteres») y, además del OSD, una
   entrada de menú marcada como en `mu-subs`, para que no se pierda si el aviso pasa.
4. **Copiar de una vez**: un botón «Copiar para la IA» que deje en el portapapeles `prompt + DURACIÓN TOTAL +
   TRANSCRIPCIÓN` (el flujo de F2) y diga cuántas líneas van y qué hacer después.
5. **Instrucciones dentro de la app**: una pantalla o entrada de menú «Capítulos: cómo funciona» con los 4 pasos y el
   prompt listo para copiar. Debe decir, en lenguaje llano: se copia el prompt junto con la transcripción, se pega en
   una IA (recomendadas: **ChatGPT** gratis; alternativa **Gemini**; sin cuenta, **Duck.ai**), se copia su respuesta y
   se pega en Atalaya con «Pegar capítulos», y aparecen los capítulos del vídeo.
6. **Reutilizar, no duplicar**: no se escribe un segundo transcriptor. `mu-subs` ya transcribe, cachea y guarda el SRT;
   este script solo encadena: transcribir → copiar → pegar.

NO hacer: abrir el navegador automáticamente; modelos locales; llamadas a APIs de IA; claves de ningún tipo;
dependencias nuevas; tocar uosc; panel de resumen.

## Criterios de aceptación

- [ ] Con un vídeo sin subtítulos: un solo botón y al final hay transcripción, con progreso visible y aviso al terminar.
- [ ] Con un vídeo que ya trae subtítulos: no se transcribe nada, se usan los del vídeo y se dice que se han usado esos.
- [ ] Un botón deja prompt + transcripción en el portapapeles; pegados en una IA, la respuesta se pega de vuelta con el
      otro botón y los capítulos salen en la barra de uosc.
- [ ] En IPTV/radio en directo el script no hace nada ni deja errores en el log (`h.script_errors() == []`).
- [ ] Todo texto visible va por `mu.i18n`, con sus cadenas en `locales/en.json` y `locales/fr.json`.
- [ ] `tools/check.sh` sin fallos nuevos: los 23 sin red y 4 con red ya conocidos y ajenos siguen igual.

## Forma de trabajar

Rama `feat/capitulos-ia`. Commits pequeños en español. Sin merge, sin push.

## Al terminar

Resumen llano, con los comandos de prueba en un solo bloque.
