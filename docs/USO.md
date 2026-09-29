# Guía de uso de MPV-UOS

Guía por tareas. Todas las teclas están en `docs/ATAJOS.md`; los detalles técnicos de cada función, en su documento (enlazado en cada
apartado). Sin mpvd (el daemon) lo básico sigue funcionando: reproducir, menús de uosc, teclas de mpv.

## 0. Instalar y abrir
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/install.sh                 # uv sync + vendor.sh + ~/.local/bin/mpv-uos + entrada "MPV-UOS" en el menú de aplicaciones
tools/install.sh --extras        # además traducción offline y búsqueda semántica (≈220 MB)
mpv-uos video.mkv                # o bin/mpv-uos sin instalar
mpv-uos                          # sin archivo: pantalla de inicio con los recientes
```
Opciones del instalador: `--xdg` (caché y datos en `~/.cache/mpv-uos` y `~/.local/share/mpv-uos` en vez de `<proyecto>/.cache`),
`--default` (reproductor por defecto para vídeo y audio), `--dry-run`, `--uninstall` (solo borra lo que creó).

## 1. Moverse por la interfaz
- **Menú MPV-UOS**: botón derecho, tecla `MENU` o `alt+m`. Reúne todo lo de esta guía.
- **Paleta** (`alt+p`): escribe para buscar comandos, canales, recientes, acciones de mpvd y (si hay transcripción) frases del diálogo.
- **Continuar viendo** (`alt+h`): los recientes se reconocen por contenido, aunque renombres o muevas el archivo.

## 2. Ver la tele y escuchar la radio
1. `alt+t` → *España TV* / *España radio* (TDTChannels), *Mundo* (iptv-org por país y categoría), *Radio mundial* (Radio Browser).
2. `alt+f` busca un canal por nombre sin acentos; `Tab` sobre un canal: favorito o copiar URL.
3. `alt+↑` / `alt+↓` cambian de canal dentro del grupo; `alt+r` graba el directo en `~/Escritorio/MPV-UOS`.
4. Tus listas: *TV y radio → Mis listas → Añadir* (pega la URL de una M3U). Más en docs/FUENTES_IPTV.md.

## 3. YouTube y otras webs (yt-dlp)
- `mpv-uos 'https://www.youtube.com/watch?v=…'` reproduce con el yt-dlp vendorizado (se actualiza solo a diario).
- `alt+a` alterna vídeo / solo audio sin perder la posición; `alt+q` elige cualquier formato; `alt+d` descarga (vídeo por resolución,
  audio original o convertido a MP3/Opus/M4A/FLAC/WAV con el bitrate que quieras, subtítulos, capítulos, SponsorBlock…);
  `alt+l` muestra la cola con progreso. Destino: `~/Vídeos/MPV-UOS` y `~/Música/MPV-UOS`. Más en docs/YTDLP.md.

## 4. Subtítulos con IA, traducción y duales
1. `alt+c` inicia los subtítulos IA del archivo local abierto (whisper.cpp; el modelo se elige según tu CPU, docs/BENCHMARKS.md).
   Aparecen unos segundos por delante de lo que ves; tras un salto, se transcribe primero lo nuevo. Quedan guardados para la próxima vez.
2. `alt+i` abre el menú: idioma, modelo (descarga bajo demanda), pre-subtitular el siguiente de la lista.
3. `alt+x` resincroniza un subtítulo descargado que va desfasado (retraso, deriva o cortes) contra la transcripción IA.
4. Menú → *Traducir la pista seleccionada a…* (offline, requiere `--extras`) y *Subtítulos duales* (original arriba, traducción abajo).
Más en docs/WHISPER.md y docs/TRADUCCION.md.

## 5. Buscar dentro del vídeo y capítulos automáticos
Con la transcripción hecha, la paleta (`alt+p`) busca por **idea** en la sección *Diálogo* ("cuando hablan del presupuesto") y Enter salta
allí. Menú de subtítulos IA → *Capítulos por tema* crea capítulos titulados con una frase del propio vídeo. Requiere `--extras` y el
modelo de embeddings (se descarga desde el menú). Más en docs/SEMANTICA.md.

## 6. Series: saltar intro y créditos
Abre un episodio: mpvd compara su audio (huellas Chromaprint, requiere `fpcalc`) con los episodios vecinos y detecta la intro y los
créditos. La primera vez tarda unos segundos por episodio (≈10 s en una serie de 50 min); después sale de la caché al instante.
- **Dónde busca los otros episodios**: primero en la misma carpeta; si no hay ninguno de la misma serie, en las carpetas hermanas (una
  carpeta por episodio, como `Descargas/Don Matteo 1x03 …/…mp4`). Se reconocen `S01E02`, `1x02`, `Temporada 1 Capítulo 2` y `Cap.102`;
  el título se compara sin acentos, puntuación ni etiquetas entre corchetes, y debe coincidir la temporada. Se usan los 3 vecinos más
  cercanos por número de episodio que se puedan leer (los rotos o sin audio se saltan). Con nombres sin serie (`ep01.mkv`) se usan los
  demás vídeos de la carpeta.
- **Qué verás**: el botón ⏭ está siempre en la barra con vídeos locales; si aún no hay nada que saltar, su tooltip dice por qué
  (analizando, sin otros episodios, error). Dentro de la intro o los créditos se ilumina y aparece un recuadro **Saltar intro ▸**
  clicable. `alt+k` salta (fuera de un segmento, al final del siguiente; durante el análisis dice «Analizando… NN %»). Si no se detecta
  nada, un aviso breve lo explica (p. ej. «Saltar intro: no hay otros episodios para comparar · alt+j»).
- **Saltar créditos** lleva al siguiente elemento de la lista; si no hay lista, abre el siguiente episodio encontrado (aunque esté en
  otra carpeta). Unos créditos que acaban a menos de 15 s del final cuentan como final del episodio.
- **Salto automático** (menú `alt+j` o `script-opts`: `mu-intro-auto_skip_intro=yes`, `mu-intro-auto_skip_credits=yes`): muestra
  «Saltando intro en 3 s · Esc cancela» (`mu-intro-countdown_seconds`, 0 = sin espera; en pausa la cuenta se detiene). Esc solo actúa
  durante la cuenta atrás.
- **Marcar a mano** (menú `alt+j` → *Marcar inicio/final de la intro/créditos aquí*, o los bindings `mu_intro/intro-mark-start`,
  `intro-mark-end`, `credits-mark-start`, `credits-mark-end`): si solo marcas el final de la intro, empieza en 0:00; el inicio de los
  créditos se guarda hasta el final del archivo (marca también su final si hay escena poscréditos). mpvd busca ese mismo audio al
  principio (intro) o al final (créditos) de los demás episodios de la temporada, aunque la intro empiece en otro minuto (avances de
  distinta duración), y también en los episodios que añadas después. Las marcas manuales mandan sobre la detección automática; se
  guardan en `<datos>/intro-marks.json` y se borran desde el menú.
- **Analizar temporada** (menú): analiza en segundo plano todos los episodios de la temporada. Al abrir un episodio, además, se preparan
  en prioridad baja las huellas de toda la temporada, así los siguientes se resuelven al momento.
- **Exportar segmentos (Jellyfin)** (menú): escribe `segments.json` junto al vídeo con los segmentos de los vídeos de esa carpeta
  (`type`/`start`/`end` en segundos y `Type`/`StartTicks`/`EndTicks` al estilo media-segments de Jellyfin). MPV-UOS nunca escribe en tus
  carpetas si no se lo pides.

Límites: una **película suelta** (sin otros episodios) no tiene intro detectable: solo sirve el marcado manual. Varias **versiones del
mismo vídeo** en una carpeta (montajes, cortes para redes) se reconocen y no se marca nada («parecen versiones del mismo vídeo»). Se
descartan coincidencias sin sentido: una intro que empiece después del 40 % del vídeo o unos créditos que acaben antes del 60 %. La intro
se busca en los primeros 10 minutos y los créditos en los últimos 5. Hace falta pista de audio; solo archivos locales.

## 7. Sonido e imagen
`alt+v`: diálogo claro, modo noche (`alt+n`), reducción de ruido, sonido binaural para auriculares, protección fotosensible, perfil ligero
para equipos modestos y diagnóstico de tirones con recomendaciones. Más en docs/AUDIO_VIDEO.md.

## 8. Estudiar con vídeos
`alt+e` menú Estudio: `alt+w` repite en bucle la frase actual (`alt+←/→` anterior/siguiente), `alt+g` acelera solo los silencios,
`alt+b` guarda una nota con la cita y un enlace de tiempo en Markdown, `alt+u` exporta el bucle A-B (tecla `l` de mpv) o la frase como
clip mp4/GIF/mp3. Más en docs/ESTUDIO.md.

## 9. Mando a distancia desde el móvil
`alt+z` muestra un QR; escanéalo con el móvil en la misma wifi y tendrás play/pausa, saltos, volumen, pistas, canales y búsqueda.
El móvil queda emparejado hasta que lo olvides (`alt+Z`). Si no conecta, abre el puerto en el cortafuegos: docs/REMOTE.md.

## 10. Asistentes (MCP)
Copia `.mcp.json.example` a `.mcp.json` para que Claude Code u otro cliente MCP controle el reproductor (reproducir, buscar en el
diálogo, canales, descargas, notas). Lo que interrumpe lo que ves pide confirmación en pantalla. Más en docs/MCP.md.

## Dónde se guardan las cosas
| Qué | Dónde (instalación por defecto) | Con `--xdg` |
|---|---|---|
| Caché (listas, transcripciones, índices, huellas) | `<proyecto>/.cache/` | `~/.cache/mpv-uos/` |
| Datos (favoritos, recientes, notas, móviles emparejados, marcas de intro) | `<proyecto>/.cache/data/` | `~/.local/share/mpv-uos/` |
| Descargas y clips | `~/Vídeos/MPV-UOS`, `~/Música/MPV-UOS` | igual |
| Grabaciones de TV/radio | `~/Escritorio/MPV-UOS` | igual |
| Configuración de mpv | `<proyecto>/mpv-config/` (nunca `~/.config/mpv`) | igual |

## Problemas frecuentes
- **No aparece nada de mpvd** (menús vacíos, "mpvd no está conectado"): `.venv/bin/python -m mpvd status`; log en `.cache/mpvd.log`.
- **Los subtítulos IA van lentos**: elige un modelo más pequeño en `alt+i` o activa el perfil ligero (`alt+v`); ver docs/BENCHMARKS.md.
- **Un canal no carga**: puede estar caído o geobloqueado; la entrada *Comprobar canales en segundo plano* de los menús de TV los marca en segundo plano.
- **YouTube falla**: `.venv/bin/python -m mpvd call ytdl.update.check` y luego `ytdl.update.apply` actualizan yt-dlp; necesita `node ≥ 22` o `deno`.
