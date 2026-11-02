# Atalaya como gestor de descargas: diseño completo

Versión 2, 31-oct-2026. Sustituye a `DISENO-YTDL-POLITICAS.md` (v1), que queda como diagnóstico.
Todo lo que se afirma de yt-dlp está comprobado contra el binario instalado (`yt-dlp --help`, 2026.10.26):
`--download-sections` acepta **regex de capítulos** y **rangos `*inicio-fin`** (repetible, tiempos negativos
desde el final), `--split-chapters` parte por capítulos con prefijo `chapter:` en la plantilla, y
`--break-on-existing` corta la descarga al llegar a lo que ya está en el archivo.

## 1. Principio: tres naturalezas, nunca mezcladas

Hoy todo es «un preset». La app tiene que distinguir **qué se está haciendo con los datos** y decirlo en la
interfaz, en el nombre del fichero y en los metadatos:

| Naturaleza | Qué es | Recodifica | Cómo se muestra |
|---|---|---|---|
| **Nativo** | una pista tal como la sirve el origen (`251`, `140`, `137`…) | no | «Opus 128k del origen» |
| **Remux** | mismas pistas, otro contenedor (`VP9+Opus` → MKV) | no | «VP9 720p + Opus 128k → MKV» |
| **Transcode** | otra pista, otro códec, otro bitrate | sí | «MP3 192k (recodificado)» + aviso de generación |

Reglas: el defecto es nativo/remux; el transcode existe, se elige explícitamente y **nunca se disfraza de
nativo**. En la cola y en el historial, cada trabajo lleva su naturaleza y, si se recodificó, con qué ajustes.

## 2. Modelo

```
Source   = lo que ofrece el origen: pistas de vídeo y audio con códec, altura, fps, kbps, bytes, idioma
Plan     = decisión: (video_id | perfil) + (audio_id | regla) + contenedor + rangos + postproceso
Job      = ejecución del Plan: cola, progreso, reintentos, log
Item     = resultado: fichero(s), tamaño, duración, naturaleza, hash
```

`Source` sale del `-J` que ya se pide (`info.py` expone `tbr/abr/channels/filesize`). `Plan` lo construye
`policy.py`, que es lo único que decide formatos. `Job` e `Item` viven en `downloads.py` y en un índice local.

## 3. Qué se puede descargar (selección de contenido)

1. **Un vídeo**.
2. **Una lista o un canal**: todo, un rango (`-I 1:20`, `-I 15:`, `-I :10`), o filtrado — por fecha
   (`--dateafter`), por duración mínima/máxima, por título (regex), por «solo lo que no tenga ya».
3. **Un directo**: en curso (`--live-from-start` graba desde el principio) o esperando a que empiece
   (`--wait-for-video`).
4. **Una búsqueda** (`ytsearchN:`), con los mismos filtros.
5. **Capítulos** de un vídeo (§4).
6. **Fragmentos** de tiempo de un vídeo (§5).
7. **Varios enlaces a la vez** (cola por lotes, ya existe `ytdl.download.batch`).

## 4. Capítulos: la función que casi nadie tiene bien

yt-dlp da los capítulos en el `-J` (`chapters: [{start_time, end_time, title}]`) y sabe cortar por ellos:

- **Ver y elegir**: lista de capítulos con duración y casilla. Selección por número, por rango (`3-7`), por
  regex de título (`"entrevista"`), o «todos menos el intro».
- **Dos modos de entrega**:
  - **Un fichero por capítulo** (`--split-chapters`), con plantilla `chapter:%(section_number)02d - %(section_title)s`.
    Ideal para podcasts, clases y series de conferencias.
  - **Un solo fichero** con los capítulos elegidos unidos y sus marcas conservadas (`--download-sections` por
    regex de capítulo + concatenación, o `--split-chapters` y unión posterior). Ideal para «los tres bloques
    que me interesan de este vídeo».
- **Con pistas**: los capítulos se combinan con cualquier perfil (Opus del origen, VP9/Opus, compatible).
- **Metadatos**: cada trozo conserva título del vídeo, número de capítulo y fecha; el `.srt` se recorta al
  mismo rango.

## 5. Fragmentos de tiempo: descargar solo lo que interesa y unirlo

- **Rangos múltiples** en una sola pasada: `--download-sections "*00:10-02:30"` repetido. Acepta tiempos
  negativos desde el final (`*-00:30-inf` = últimos 30 s).
- **Unir al terminar**: concatenación sin recodificar (concat demuxer, `-c copy`) cuando todos los trozos
  comparten códec y parámetros, con verificación de duración; si no comparten, se avisa y se ofrece
  recodificar solo la unión (transcode explícito).
- **Margen y ajuste**: 0,3 s de cola para no cortar la última sílaba; opción «desde el capítulo» para no
  calcular tiempos a mano; y «empezar en el punto actual» cuando lo que se descarga es lo que se está viendo.
- **Aviso técnico que hay que respetar**: con VP9/Opus, `--download-sections` y copia de flujo dan rangos
  imprecisos (medido antes: 16 s en vez de 6); con H.264+AAC en MP4 van a ±0,03 s. Por eso los fragmentos
  usan por defecto **H.264+AAC** (naturaleza remux), y si el usuario pide VP9 se le dice que el corte irá por
  fotograma clave.

## 6. Transcodificar: catálogo explícito, nunca el defecto

Pantalla aparte, «Convertir», con todo lo que se pueda querer y estimación antes de empezar:

- **Audio**: Opus (32/48/64/96/128/160), AAC/M4A (96/128/192/256), MP3 (128/192/256/320/VBR), FLAC, WAV,
  ALAC. Canales: conservar / estéreo / mono. Loudness: ninguna, `-18 LUFS` (voz), `-14` (música).
- **Vídeo**: H.264, H.265, VP9, AV1; resolución (conservar o 2160/1440/1080/720/480/360); CRF o bitrate;
  fps (conservar, 60, 30); dos pasadas opcional; hardware si existe (NVENC/QSV/VAAPI) con aviso de que
  cambia la calidad por vatio.
- **Contenedor**: MP4, MKV, WebM, MP3/M4A/Opus.
- **Extras**: quemar subtítulos, recortar por capítulos antes de convertir, normalizar audio, «quitar
  SponsorBlock» en el corte.
- **Estimación**: tamaño y tiempo aproximados (`kbps × duración`, y para vídeo un factor por resolución y
  CRF), con el aviso de que es una estimación.

## 7. Suscripciones: seguir un canal y bajar lo nuevo

**Sí, y sin API ni cuentas.** YouTube publica un feed por canal:

```
https://www.youtube.com/feeds/videos.xml?channel_id=UC...
```

Trae las **15 últimas** entradas (id, título, fecha, autor). Eso basta para «seguir»: se sondea cada X horas,
se comparan los ids con el índice local y se encolan los nuevos. Para el fondo histórico no se usa el feed
(15 es poco): se usa la URL del canal con `--dateafter` + `--download-archive`, que sí recorre todo.

- **Regla por canal**: perfil (Opus del origen / 720p / compatible), carpeta, filtros (duración mínima,
  regex de título para excluir directos o shorts), tope por vídeo y por descarga, y si se avisa o no.
- **Programación**: cada 30 min / 6 h / diario, con ventana de horas (no bajar de madrugada si molesta) y
  límite de ancho de banda.
- **Incremental de verdad**: `--download-archive` por canal + `--break-on-existing` para cortar al llegar a
  lo conocido; así una comprobación de 200 vídeos cuesta segundos.
- **Otros sitios**: donde haya feed (RSS/Atom) se usa el mismo motor; donde no, la URL del canal o playlist
  con los mismos filtros. `sites.py` ya sabe qué extractores existen.
- **Aviso**: notificación de escritorio al terminar, con «3 nuevos de <canal>» y enlace a la carpeta.

## 8. Lo que convierte esto en un gestor de descargas de verdad

1. **Índice local (SQLite)**: id de vídeo, canal, título, ruta, perfil, tamaño, fecha, hash. Sirve para
   deduplicar entre carpetas («ya lo tienes en X»), para saber qué falta de una lista y para el historial.
2. **Cola con prioridades**: descarga en curso, en espera, pausada; reordenar; pausar todo; límite de
   velocidad global o por trabajo; concurrencia configurable (hoy 2 fija).
3. **Reintentos con criterio**: 403/429 → cambiar de cliente de YouTube (`--extractor-args
   youtube:player_client=...`) o esperar; vídeo retirado o privado → fallo definitivo con motivo; red caída →
   reintento con espera creciente. Nunca un bucle infinito.
4. **Cookies y cuentas**: `--cookies-from-browser` para edad restringida o miembros (Firefox/Chrome/Edge);
   varios perfiles de cookies; aviso claro de qué se está usando.
5. **Verificación post-descarga**: `ffprobe` abre el fichero, comprueba duración y pistas esperadas y que
   decodifica 10 s; si no, se reintenta una vez y se marca como fallido. No basta «terminó sin error».
6. **Sidecars**: `.info.json`, descripción, miniatura (y convertida a jpg para televisores), subtítulos
   `.srt`, capítulos incrustados, SponsorBlock marcar/quitar.
7. **Organización**: carpeta por canal o lista, numeración por orden de la lista, plantilla configurable,
   `artist/album` para música, y «usar la fecha de subida» o «la de descarga» para ordenar.
8. **Previsualización**: antes de encolar, ficha con pistas reales, contenedor, tamaño estimado y tiempo
   estimado; para listas, «N vídeos · ~X GB» y comprobación de espacio (estimado × 1,2 + 2 GB).
9. **Guardas de disco**: tope por descarga, tope por canal, «borrar lo ya visto» y aviso al 90 % de la
   carpeta elegida.
10. **Reproducir lo descargado**: los ficheros entran en la biblioteca con metadatos, posición de reproducción
    y búsqueda; «descargado el 31-oct» como criterio de orden.
11. **Transcripción opcional**: al terminar, si se pide, se lanza el ASR local (`mpvd/asr`) y se deja el
    `.srt`; sirve para buscar dentro de lo descargado, que es justo lo que ya hace la biblioteca.
12. **Modo «solo mirar»**: `--simulate` para ver qué se bajaría, sin tocar el disco.

## 9. Arquitectura

```
mpvd/ytdl/
  policy.py        NUEVO  clasificar(), pistas(), plan(), contenedor_valido()   ← una sola verdad
  chapters.py      NUEVO  capítulos → selección, split, unión, metadatos
  sections.py      NUEVO  rangos, márgenes, precisión por códec, unión
  subscriptions.py NUEVO  feeds, reglas por canal, sondeo, encolado incremental
  index.py         NUEVO  SQLite: items, dedupe, historial, «qué falta»
  presets.py       EDIT   presets nuevos + video_id/audio_id + build_args con validación
  service.py       EDIT   RPC ytdl.plan, ytdl.pistas, ytdl.chapters, ytdl.sections,
                          ytdl.subs.*, ytdl.index.*; ytdl.download acepta ids y rangos
  downloads.py     EDIT   topes, archivo por defecto, informe por ítem, verificación, índice
mpv-config/scripts/mu-ytdl/main.lua  EDIT  vistas: Descargar · Pistas · Capítulos · Fragmentos ·
                                           Suscripciones · Cola · Historial · Convertir
```

## 10. Interfaz (uosc), el flujo que importa

1. Se pega un enlace (o se pulsa sobre lo que se está viendo). La app dice qué es: *vídeo · canal · lista de
   32 · directo*.
2. Tres botones: **Opus del origen** · **Vídeo 720p VP9+Opus** · **Compatible (H.264+AAC)**.
3. Debajo: **Pistas del origen…**, **Capítulos…** (si los hay), **Fragmentos…**, **Convertir…**.
4. Al elegir, ficha: *«VP9 720p 1,2 Mbps + Opus 128k → MKV · ~210 MB · ~2 min»*. Confirmar.
5. En canal o lista: *«32 vídeos · ~6,4 GB · solo los que no tengas»*, con tope y filtros.
6. Cola con progreso, pausa, cancelar, reintentar y el informe de fallidos al final.

## 11. Fases

| Fase | Trabajo | Hecho cuando |
|---|---|---|
| **F1** | `policy.py`, presets nuevos, `video_id/audio_id`, tests | tabla de contenedores y voz/música en verde; `-f` correcto en 6 casos |
| **F2** | `chapters.py` + `sections.py` + RPC + vistas | un vídeo con 8 capítulos → 8 ficheros correctos; 2 fragmentos unidos con duración exacta |
| **F3** | pistas + ficha previa + transcode explícito | descarga real de música y de voz en `.opus` sin recodificar (ffprobe) |
| **F4** | `subscriptions.py` + `index.py` + cola con prioridades | un canal de prueba baja 2 nuevos y no repite lo ya descargado |
| **F5** | guardas de disco, informe, verificación post-descarga | lista de 10 con 2 fallos a propósito: el informe los nombra |
| **F6** | probar `compatible` en TV/Windows; docs y ADR | abre en televisor sin avisos; decisión escrita |

Esfuerzo: F1 4-6 h · F2 8-10 h · F3 3-4 h · F4 8-10 h · F5 3-4 h · F6 según hardware.

## 12. Riesgos y decisiones abiertas

- **Fragmentos con VP9/Opus** son imprecisos por diseño de yt-dlp: se resuelve con H.264+AAC por defecto y
  aviso si se pide VP9.
- **PO tokens y cambios de YouTube**: el modo `--extractor-args youtube:player_client` y la actualización
  nocturna del binario ya existen en la app; hay que enseñar el fallo con claridad («YouTube ha cambiado;
  actualiza yt-dlp») en vez de un error crudo.
- **Cookies**: sin ellas no hay edad restringida ni miembros. Se soportan por navegador, pero es decisión del
  usuario activarlo.
- **RSS solo trae 15 entradas**: es para «novedades», no para reconstruir un canal. El fondo se hace con la
  URL del canal y archivo.
- **Espacio**: un canal grande puede comerse el disco; de ahí los topes por canal y el aviso al 90 %.
