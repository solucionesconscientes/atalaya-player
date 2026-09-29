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
- **Abrir URL** (`ctrl+u`, o `alt+y` → *Abrir URL…*): si el portapapeles tiene un enlace, la primera entrada es *Pegar: …*;
  también puedes escribir o pegar (`ctrl+v`) una URL (YouTube, Twitch, archive.org, una radio, `rtsp://`…; vale `youtu.be/…` sin
  `https://`) y pulsar Enter. Si lo escrito no es una URL, la entrada pasa a ser *Buscar «texto» en YouTube*.
  `Tab` sobre una URL: *Añadir a la lista* (se reproduce al terminar lo actual) o *Descargar*.
- **Buscar en YouTube** (`ctrl+f`, o `alt+y` → *Buscar en YouTube…*): escribe y pulsa Enter; salen hasta 15 resultados con
  duración y canal (*EN DIRECTO* en los directos). Enter reproduce el elegido; `Tab`: *Añadir a la lista* o *Descargar* (abre el
  menú de descarga de ese vídeo sin tocar lo que suena). La misma búsqueda durante una hora sale al instante (caché de mpvd).
- `alt+a` alterna vídeo / solo audio sin perder la posición; `alt+q` elige cualquier formato; `alt+d` descarga (vídeo por resolución,
  audio original o convertido a MP3/Opus/M4A/FLAC/WAV con el bitrate que quieras, subtítulos, capítulos, SponsorBlock…);
  `alt+l` muestra la cola con progreso. Destino: `~/Vídeos/MPV-UOS` y `~/Música/MPV-UOS`. Más en docs/YTDLP.md.
- Contenedor de las descargas de vídeo: **mp4** prioriza H.264 + AAC (se abre en cualquier sitio; en YouTube el máximo es 1080p),
  **webm** VP9 + Opus y **mkv** la mejor calidad sin restricciones (AV1, 4K…). Se cambia en *Descargar → Opciones → Contenedor*.

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
Abre un episodio de una carpeta con varios: MPV-UOS compara su audio con los demás y detecta la intro y los créditos. Al llegar aparece el
botón ⏭; `alt+k` salta (en los créditos, al siguiente episodio) y `alt+j` abre el menú (salto automático, reanalizar). Requiere `fpcalc`.

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

## 11. Preferencias: qué se recuerda y qué no
Lo que eliges se queda para la próxima vez que abras el reproductor. Se guarda en `prefs.json`, en la carpeta de datos
(`$MPV_UOS_DATA_DIR`; por defecto `~/.local/share/mpv-uos/`). Lo que pases en la línea de órdenes (`mpv-uos --volume=40 …`,
`--script-opts=…`) manda sobre lo recordado, solo en esa sesión. Con varias ventanas abiertas se guardan los cambios de todas; si dos
cambian lo mismo, gana el último.

| Se recuerda para todo lo que abras | Cómo se cambia |
|---|---|
| Volumen, silencio y velocidad | `9`/`0`, `m`, `[`/`]`, barra de uosc, mando del móvil |
| Subtítulos: tamaño, posición y visibilidad (también de los secundarios), estilo ASS, márgenes | teclas y menús de mpv/uosc |
| Imagen: contraste, brillo, gamma, saturación, tono | teclas de mpv |
| Pantalla completa, siempre encima, repetir la lista, decodificación por hardware (`auto-safe`/`no`), formato de yt-dlp | `f`, menús |
| Idiomas de audio y subtítulos: al elegir una pista con idioma, ese idioma pasa a ser el preferido | `a`, `s` |
| Subtítulos quitados: los siguientes archivos empiezan sin subtítulos hasta que actives una pista | `s` → ninguno |
| Filtros de *Sonido e imagen* y perfil ligero | `alt+v`, `alt+n` |
| Velocidad inteligente (se reactiva en el siguiente archivo local) y su velocidad en silencios | `alt+g`, `alt+e` |
| Continuar viendo activado o no | *Menú → Preferencias* |

| No se recuerda, a propósito | Por qué |
|---|---|
| Pausa, posición, pista concreta, retardos de audio/subtítulos, zoom, encuadre, aspecto, rotación, desentrelazado | dependen de cada archivo: mpv los guarda por archivo al salir y *Continuar viendo* recuerda la posición |
| Repetir archivo (`L`) y aleatorio | te quedarías repitiendo o mezclando todo lo que abras después |
| Cambios automáticos: al cargar un archivo, opciones por archivo, la velocidad que pone la velocidad inteligente en los silencios, pistas creadas por la IA | no son elecciones tuyas |

**Restablecer**: *Menú (`alt+m`) → Preferencias → Restablecer preferencias…* o, en la paleta (`alt+p`), "Restablecer preferencias".
Pide confirmación, mueve `prefs.json` a `prefs.json.bak-<fecha>` (no se borra nada: para recuperarlo, renómbralo) y vuelve al momento a
los valores de `mpv.conf`. Desde la consola de mpv: `script-message-to mu_prefs reset`. Para no recordar nada: `MPV_UOS_PREFS=0` o
`--script-opts=mu-prefs-enabled=no`. Si `prefs.json` se estropea, se aparta como `prefs.json.corrupt-<fecha>` y se sigue con los valores
por defecto.

## Dónde se guardan las cosas
| Qué | Dónde (instalación por defecto) | Con `--xdg` |
|---|---|---|
| Caché (listas, transcripciones, índices, huellas) | `<proyecto>/.cache/` | `~/.cache/mpv-uos/` |
| Datos (favoritos, recientes, notas, móviles emparejados) | `<proyecto>/.cache/data/` | `~/.local/share/mpv-uos/` |
| Descargas y clips | `~/Vídeos/MPV-UOS`, `~/Música/MPV-UOS` | igual |
| Grabaciones de TV/radio | `~/Escritorio/MPV-UOS` | igual |
| Configuración de mpv | `<proyecto>/mpv-config/` (nunca `~/.config/mpv`) | igual |

## Problemas frecuentes
- **No aparece nada de mpvd** (menús vacíos, "mpvd no está conectado"): `.venv/bin/python -m mpvd status`; log en `.cache/mpvd.log`.
- **Los subtítulos IA van lentos**: elige un modelo más pequeño en `alt+i` o activa el perfil ligero (`alt+v`); ver docs/BENCHMARKS.md.
- **Un canal no carga**: puede estar caído o geobloqueado; la entrada *Comprobar canales en segundo plano* de los menús de TV los marca en segundo plano.
- **YouTube falla**: `.venv/bin/python -m mpvd call ytdl.update.check` y luego `ytdl.update.apply` actualizan yt-dlp; necesita `node ≥ 22` o `deno`.
