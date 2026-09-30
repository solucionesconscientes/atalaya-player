# Modo estudio — stack verificado (H11)

Verificación del 2026-09-29 contra las fuentes instaladas: **mpv 0.41.0** (`man mpv`, `--list-properties`, `--list-options`
y pruebas headless por JSON IPC con `--no-config --vo=null --ao=null --idle=yes --pause` sobre `tests/fixtures/media/voz_es_en.mkv`
más un SRT mínimo de 3 cues: 1–3 s, 5–7 s, 10–12 s), **ffmpeg 8.0.1** y **whisper.cpp 1.9.3** vendorizado (`vendor/whisper`).

## 1. mpv: propiedades y comandos (sintaxis exacta y comportamiento observado)

| Propiedad | Existe | Observado en headless |
|---|---|---|
| `sub-text` | sí (R) | Texto del cue actual sin formato; `""` cuando no hay cue. Subprops `sub-text/ass`, `sub-text/ass-full`. |
| `sub-start`, `sub-end` | sí (R) | En t=2,0 → `1.0` / `3.0` (float, segundos). **Sin cue → error `property unavailable`** (en Lua `mp.get_property_number` da `nil`; el manual dice "null"). Subprops `sub-start/full`, `sub-end/full` (milisegundos; en el SRT dieron el mismo valor). `sub-end` también es `unavailable` si el cue no tiene duración conocida. |
| `sub-delay` | sí (RW, float s) | **`sub-start`/`sub-end` NO incluyen `sub-delay`**: con `sub-delay=1` en t=3,5 se muestra "uno" (1–3 s desplazado a 2–4) pero `sub-start` sigue devolviendo `1.0`. Para repetir la línea hay que saltar a `sub-start + sub-delay` y comparar `time-pos` con `sub-end + sub-delay`. |
| `ab-loop-a`, `ab-loop-b` | sí (RW) | Valor `"no"` (string) cuando no están; float en segundos si están. Se escriben con float o con la string `"no"`. |
| `ab-loop-count` | sí (RW) | Por defecto `"inf"` (string); admite entero (`2` → `2`). `0` desactiva el bucle incluso con A y B puestos. |
| `speed` | sí (RW, 0.01–100) | 40 cambios en 2 s por IPC (20 Hz) → 0 errores. Fuera de rango (100.1) → `unsupported format for accessing property`. |
| `audio-pitch-correction` | sí | **`yes` por defecto** (verificado con `--no-config`): con `speed≠1` inserta `scaletempo2` internamente (no aparece en `af`). |
| `chapter-list` | sí (RW) | Lista nativa `[{title, time}]`; `chapters.mkv` da `time` con −0,021 s de offset (mkv). Es escribible (sirve para capítulos propios). |
| `sub-seek` | **no es propiedad** | Es comando (abajo). |
| `secondary-sub-start/end/text` | sí | Equivalentes para el subtítulo secundario (duales). |

Comandos (todos devolvieron `success` por IPC):

- `seek <target> [<flags>]` — flags `relative` (defecto), `absolute`, `absolute-percent`, `relative-percent`, `keyframes`, `exact`; se combinan con `+`: `seek 5.25 absolute+exact`. Los `absolute` ya son exactos por defecto; los `relative` van a keyframe salvo `+exact`.
- `sub-seek <skip> [primary|secondary]` — salta vídeo y audio al cue `<skip>` relativo: `sub-seek 0` = **inicio del cue actual** (repetir línea); `sub-seek 1` = siguiente; `sub-seek -1` = **anterior** (desde mitad de un cue NO vuelve a su inicio, va al cue previo). Aterriza ~0,04 s después del inicio (5,0 → 5,04). Con subtítulos incrustados solo alcanza cues ya demuxados (caché).
- `sub-step <skip> [primary|secondary]` — no salta: **ajusta `sub-delay`** para que se muestre otro cue (desde 5,52 s, `sub-step 1` → `sub-delay=-4.49` y se ve "tres"). No sirve para repetir línea; sí para resincronizar a ojo.
- `ab-loop` — cicla A → B → limpiar (`ab-loop-a=5.0`, luego `ab-loop-b=7.0`, luego ambos `"no"`).
- `screenshot-to-file <archivo> [video|subtitles|window]` — formato por extensión (ignora `--screenshot-format`); sobrescribe. **Funciona con `--vo=null`** (JPEG 640×360 válido en la prueba).
- `script-message <arg1> [...]` — libre, a todos los clientes; `script-message-to <script> ...` a uno concreto. En Lua: `mp.register_script_message("nombre", fn)`.
- `stream-record` — es **opción/propiedad** (`set_property stream-record <archivo>`, `""` para cerrar), no comando. Solo escribe lo que se añade a la caché a partir de ese momento (no lo ya cacheado); con `--pause` no genera archivo; en `video30.mkv` escribió 4 s pero con avisos `non monotonically increasing dts` y `[mkv] Stopping recording`. **Descartado para clips: usar ffmpeg** (§2).

Atajos ya ocupados en `mpv-config/input.conf` / `docs/ATAJOS.md`: `l` bucle A-B, `L` repetir archivo, `[`/`]`/`BS` velocidad, `ctrl+s` captura.

## 2. ffmpeg 8.0.1: clips, GIF y audio (probado sobre `video30.mkv`, tramo 5–8 s)

Filtros presentes: `palettegen`, `paletteuse`, `silencedetect`, `silenceremove`, `atempo`, `rubberband`. Codificadores: `libx264`, `aac`, `libopus`, `libmp3lame`, `gif`.

| Uso | Comando (A=5, B=8) | Resultado / tiempo |
|---|---|---|
| Clip sin recodificar | `ffmpeg -y -ss 5 -to 8 -i IN -c copy OUT.mp4` | h264+aac, **0,12 s**, pero `video30.mkv` solo tiene keyframes en 0/10/20 s → el clip contiene 202 frames (desde 0 s, `first_pts=-5.0`) y reporta 3,14 s; con `-avoid_negative_ts make_zero` dura 8,1 s. **Solo es exacto si A cae en keyframe**: comprobar con `ffprobe -select_streams v -skip_frame nokey -show_entries frame=pts_time` o recodificar. |
| Clip recodificado (exacto) | `ffmpeg -y -ss 5 -to 8 -i IN -c:v libx264 -crf 23 -preset veryfast -c:a aac -movflags +faststart OUT.mp4` | 75 frames, 3,04 s, 215 KB, **0,57 s** (640×360). Recomendado por defecto. |
| GIF con paleta | `ffmpeg -y -ss 5 -to 8 -i IN -filter_complex "[0:v]fps=12,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" -loop 0 OUT.gif` | 480×270, 3,0 s, 802 KB, **1,84 s** (lo más lento; escala con duración×resolución). |
| MP3 del tramo | `ffmpeg -y -ss 5 -to 8 -i IN -vn -c:a libmp3lame -q:a 4 OUT.mp3` | 3,0 s, 13 KB, 0,13 s. |
| Opus del tramo | `ffmpeg -y -ss 5 -to 8 -i IN -vn -c:a libopus -b:a 64k OUT.opus` | 3,0 s, 28 KB, 0,17 s. |

Notas: `-ss` antes de `-i` hace búsqueda de entrada (rápida) y, aun así, `-to 8` se aplica sobre la línea de tiempo original (todas las salidas duraron 3 s, no 8): `-ss A -to B` copia literalmente el A-B de mpv sin restar. `-map 0:a:N` elige pista (como `mpvd/asr/audio.py`). Los tiempos son de un portátil de 4 núcleos sin GPU con un origen de 640×360.

## 3. Velocidad inteligente (acelerar silencios)

Lo que ya existe:
- `mpvd/asr/audio.py`: `extract_args()` → `ffmpeg -ss S -t D -i SRC -vn -map 0:a:N -ac 1 -ar 16000 -c:a pcm_s16le -f wav OUT` (ventana de audio 16 kHz mono).
- `mpvd/intro/detect.py`: `detect_edges(src, start, length, silence_db=-45, silence_min=0.4)` → `{"silence": [(a,b)...], "black": [...]}` en tiempo de medio, parseando `silence_start/silence_end` de stderr; fallback `_audio_only` si no hay vídeo.
- `mpvd/asr/engine.py`: `argv()` añade `--vad --vad-model vendor/whisper/models/ggml-silero-v5.1.2.bin` cuando el modelo existe (activo por defecto, `MPV_UOS_ASR_VAD=0` lo apaga).

Verificado:
- `whisper-cli --help` soporta `--vad`, `--vad-model`, `--vad-threshold` (0.50), `--vad-min-speech-duration-ms` (250), `--vad-min-silence-duration-ms` (100), `--vad-max-speech-duration-s`, `--vad-speech-pad-ms` (30), `--vad-samples-overlap` (0.10).
- Hay binario aparte **`vendor/whisper/bin/whisper-vad-speech-segments`** (`-np -t N -vm MODELO -f AUDIO`, admite flac/mp3/ogg/wav): sobre `voz_es.flac` (11,7 s) tardó **0,33 s** y devolvió 3 segmentos de voz en **centisegundos** (`start = 3.00, end = 377.00` → 0,03–3,77 s). Los huecos entre segmentos son los tramos sin voz.
- `silencedetect=n=-35dB:d=0.6` sobre `voz_es.flac` (RMS −21,5 dB) **no detectó nada**; con `n=-30dB:d=0.3` dio 3 pausas de ~0,39 s (3,69–4,09, 7,37–7,76, 11,28–11,68), coincidentes con los huecos del VAD (3,77–4,03, 7,45–7,71). Las pausas de habla natural son cortas: `d=0.6` solo sirve para silencios largos (introducciones, cortes).

Propuesta: servicio `study.silences(path, start, length)` en mpvd que, por delante de `time-pos` (ventana de p. ej. 60 s, en caché por hash+ventana+parámetros), extraiga WAV con `audio.extract_args` y ejecute **`whisper-vad-speech-segments`** (preferido: robusto al ruido de fondo, 30× tiempo real en CPU) o, si no hay modelo VAD, `silencedetect=n=-30dB:d=0.3` vía `detect_edges(..., silence_db=-30, silence_min=0.3)`. Devolver `[(a,b)]` de tramos sin voz ≥ 0,3 s y dejar que `mu-study` (Lua) suba `speed` (p. ej. ×2–3, sin superar 100) al entrar en un tramo y restaure al salir, con `audio-pitch-correction` por defecto (`yes`) y un timer a ≤10 Hz; cambiar `speed` varias veces por segundo no da errores (§1).

## 4. Notas en Markdown (ya implementado en H8: no duplicar)

- (Actualizado en H17, ADR-043: `mpvd/notes.py`, un archivo por vídeo con el título como nombre y cabecera `titulo/video/clave`,
  enlaces `mpv-uos://open?path=…&t=…`, métodos `notes.get/edit/delete/export` y menú «Mis notas» en `mu-notes`; los
  archivos de la primera versión se migran solos. Lo que sigue describe la versión de H8.)
- `mpvd/control.py` → `NotesStore` en `<data_dir>/notas/<clave>.md` (clave = `media_key(path)`, saneada a `[A-Za-z0-9_.-]`). Métodos JSON-RPC `notes.add(text, path?, time_pos?, title?, session?)`, `notes.list()`, `notes.read(key)`.
- Formato: cabecera `# <título o ruta>` + línea `` `<ruta>` ``; cada nota es una línea `- [HH:MM:SS](mpv://seek?t=123.4) · 2026-09-29 12:00 — texto` (o sin enlace si no hay `time_pos`).
- `docs/MCP.md`: tool `add_note {text, time_pos?}`, recurso `mpv://notes/<clave>`; `mpv://transcript/<id>` para el SRT.
- Para H11 basta ampliar: nota con **rango** (`t=A&to=B` o dos enlaces) y con la línea de subtítulo actual (`sub-text`) como cita; quien resuelva `mpv://seek?t=` en mpv (script-message hacia `mu-menu`/`mu-study`) ya recibe el float en segundos.
