# whisper.cpp en MPV-UOS (verificado contra los binarios vendorizados)

Versión vendorizada: `whisper.cpp 1.9.3-dev` (libwhisper 1.9.3, ggml 0.22) copiada por `tools/vendor_whisper.sh` desde el build de
`~/proyectos/live-captions-linux/whisper.cpp` a `vendor/whisper/bin` (ADR-023). Se ejecuta con `LD_LIBRARY_PATH=vendor/whisper/bin`
(`DYLD_LIBRARY_PATH` en macOS; en Windows basta con que las DLL estén junto al .exe). Comprobación: `tools/check.sh` (paso "vendor
(whisper.cpp)") o `LD_LIBRARY_PATH=vendor/whisper/bin vendor/whisper/bin/whisper-cli --version`.

## Opciones de `whisper-cli` que usa mpvd (salida real de `--help`)
| Opción | Uso en mpvd |
|---|---|
| `-m FNAME` | modelo ggml (`vendor/whisper/models/ggml-<nombre>.bin` o `<data_dir>/models/whisper`) |
| `-f FNAME` | WAV 16 kHz mono 16 bit extraído con ffmpeg (`mpvd/asr/audio.py`) |
| `-t N` | hilos = núcleos−1 (máx. 8); `MPV_UOS_ASR_THREADS` |
| `-l LANG` | idioma ISO o `auto` (una vez detectado se fija para el resto de trozos) |
| `-oj -of BASE -np` | JSON en `BASE.json`, sin ruido en stdout |
| `-tr` | traducir al inglés (`translate=true`) |
| `--prompt` | final (~200 caracteres, cortado en palabra) del texto del trozo anterior si ya está transcrito: mantiene nombres, estilo y puntuación entre trozos |
| `-bs N` / `-bo N` / `-nf` | beam search / best-of / sin fallback de temperatura (`MPV_UOS_ASR_BEAM`, `MPV_UOS_ASR_BEST_OF`) |
| `--vad --vad-model FNAME` | VAD Silero v5.1.2 (`ggml-silero-v5.1.2.bin`; `MPV_UOS_ASR_VAD=0` lo desactiva) |

JSON: `{"result":{"language":"es"},"transcription":[{"timestamps":{"from":"00:00:00,030","to":"…"},"offsets":{"from":30,"to":4200},"text":" …"}]}`
(`offsets` en ms; con VAD los tiempos ya están reproyectados al audio original). `mpvd/asr/engine.py::parse_cli_json`.

## Hallazgos
- **Coste fijo por llamada**: whisper procesa siempre una ventana de 30 s, así que 1 s de silencio cuesta lo mismo que 20 s de voz
  (tiny 1,3 s · base 3,0 s · small-q5_1 12 s con 3 hilos en un i5 de 4 núcleos). Por eso no compensa `whisper-server` (ADR-024) y los
  trozos son de **28,5 s** (+0,8 s antes y 0,4 s después = 29,7 s, una sola ventana; antes 20 s). Las transcripciones guardadas en caché
  con trozos de 20 s se reaprovechan: cuentan como hechos los trozos nuevos cubiertos por trozos viejos terminados.
- **FLAC directo**: `whisper-cli -f voz.flac` (decodificado por miniaudio) devolvió una transcripción vacía; con WAV funciona.
  mpvd siempre extrae WAV con ffmpeg (además así selecciona la pista de audio y la ventana temporal).
- **Voz sintética** (espeak-ng): tiny en modo `auto` detecta italiano; con el idioma fijado acierta. Los tests usan `base` si está
  vendorizado y exigen ≥3 palabras clave (≥2 con tiny).
- **VAD** agrupa la voz continua en un solo segmento largo; `mpvd/asr/srt.py::split_long` lo reparte en cues legibles (≤7 s, ≤84
  caracteres) de longitud parecida, cortando en el fin de frase, coma o conjunción más cercano (±3 palabras) en vez de llenar 84
  caracteres y dejar un resto corto.
- `whisper-server` (mismo build) admite `--vad`, `--convert` (necesita ffmpeg) y `--inference-path`; no se usa (ADR-024).

## Modelos
Catálogo y URLs oficiales (Hugging Face `ggerganov/whisper.cpp` y `ggml-org/whisper-vad`) en `mpvd/asr/models.py`; descarga bajo
demanda con verificación de la magia `lmgg` y escritura atómica. Elección por tier de hardware (`mpvd/hardware.py`) según
docs/BENCHMARKS.md: en un portátil de 4 núcleos, en vivo `base` y pre-subtitulado `small-q8_0`. Con `model=auto`, `asr.start`
reutiliza una transcripción terminada de ese archivo hecha con otro modelo (la del pre-subtitulado) antes que empezar otra peor. Menú "Subtítulos IA → Modelo" para descargar/borrar. `tools/bench_asr.sh` regenera los benchmarks.

## Servicio `asr.*` (mpvd)
`asr.status [id]` · `asr.models` · `asr.models.download name [notify]` · `asr.models.remove name` · `asr.start path [language model
time_pos audio_track translate chunk_seconds purpose notify]` · `asr.precompute path […]` · `asr.seek id time_pos` · `asr.stop id` ·
`asr.segments id [start end]`. Eventos push a `notify` (`script-message-to <script> mu-event <json>`): `{"event":"asr","task":{…}}`
y `{"event":"asr-model","model":…,"job":{…}}`. El SRT vive en `<cache>/asr/<hash>/<modelo>.<idioma>.srt` (escritura atómica) y el
estado (cues + trozos hechos) en la caché de artefactos, por lo que una tarea se reanuda al instante tras cerrar mpv o mpvd.

## Servicio `subs.*` (H6)
`subs.info {srt}` · `subs.shift {srt, offset, speed}` · `subs.resync {path, srt, language?, model?}` (alinea un subtítulo externo con los
segmentos Whisper del archivo; `pending` + tarea `asr` si aún no hay transcripción) · `subs.translate*` (docs/TRADUCCION.md).
Algoritmo de resync en ADR-026 y `mpvd/subs/resync.py`.

## Guardar y extraer (subs.save / subs.extract, 2026-09-30)
`subs.extract {path, ff_index, codec?}` → SRT en `<cache>/subs/<hash>/track<ff_index>.srt` con `ffmpeg -i V -map 0:<ff-index> -c:s srt
-f srt` (trabajo con progreso, eventos `subs-extract`; `ff_index` = `track-list/N/ff-index` de mpv 0.41). Así la traducción y el
resync funcionan con pistas de texto internas. `subs.save {path, kind: ai|translation|resync|track, srt?, ff_index?, codec?, lang,
dest_dir?, overwrite=false, title?, allow_partial?, complete?}` escribe `<vídeo>.<idioma>.srt` de forma atómica (ASS/VTT → SRT;
SRT tal cual); si existe, `.ia`/`.resync` y luego ` (2)`; carpeta no escribible o URL → `~/Vídeos/MPV-UOS/Subtítulos`
(`XDG_VIDEOS_DIR`; `MPV_UOS_SUBS_SAVE_DIR` en tests). Pista IA sin terminar → `partial` + `coverage`; `complete=true` la deja
terminar (reanudándola en baja prioridad) y guarda al acabar (evento `subs-save`). Subtítulos de imagen (`hdmv_pgs_subtitle`,
`dvd_subtitle`, `dvb_subtitle`) → error "subtítulo de imagen: necesita OCR". La tarea IA recuerda lo guardado (`saved`) para que
mu-subs seleccione ese archivo, cargado por `sub-auto`, en vez de añadir la misma pista otra vez.

## Tiempos por palabra y sincronía (H16, ADR-042)
Verificado con el whisper-cli vendorizado (1.9.3-dev) — notas completas de la verificación en `tmp/whisper-word-timing.md`
(no versionado):
- No hay `--token-timestamps`: `-ojf` (`--output-json-full`) activa los tiempos por token y añade a cada segmento
  `tokens[] = {text, timestamps, offsets:{from,to} (ms, resolución 10 ms), id, p, t_dtw}`. Los especiales `[_BEG_]` y
  `[_TT_N]` van dentro de `tokens[]`. Una palabra empieza en el token cuyo texto empieza por espacio.
- `--dtw <tiny|base|small|medium|large.v3|large.v3.turbo|….en>` rellena `t_dtw` (centisegundos, marca el FINAL del token)
  solo con `-nfa`: con la atención flash (activada por defecto en esta versión) avisa `dtw_token_timestamps is not
  supported with flash_attn - disabling` y deja −1. Funciona con los cuantizados (`ggml-small-q8_0.bin` + `--dtw small`).
  `-nfa` cuesta 2–3 s más por trozo de 11 s en este portátil.
- Con `--vad`, los segmentos vuelven al tiempo original, pero los tokens (y `t_dtw`) quedan en el tiempo «solo voz»
  (tramos pegados). Sin `-np`, el log trae la tabla `whisper_vad: vad_segment_info: orig_start: …, orig_end: …,
  vad_start: …, vad_end: …` y los tramos de Silero, que MPV-UOS usa para devolver cada palabra a su tiempo.

Cómo se construyen los subtítulos (`mpvd/asr/timing.py`):
1. Palabras desde los tokens (`t_dtw` si existe); con VAD, al tiempo original con la tabla del log.
2. Tramos de voz: los de Silero, partidos en las pausas ≥ 0,25 s que ve una VAD de energía sobre el WAV (Python puro,
   ~0,1 s por trozo); sin modelo VAD, la VAD de energía y `--dtw <tamaño> -nfa`.
3. Cada hueco entre tramos se asigna al límite entre palabras más cercano (un fin de frase o una coma ayudan; tras un
   artículo o preposición, no) y las palabras se encajan en su tramo: el tramo empieza con su primera palabra.
4. Agrupación: una pausa ≥ 0,6 s corta siempre; ≥ 0,25 s corta tras fin de frase, tras coma si ya hay 25 caracteres o
   en cualquier sitio si ya hay una línea (42). Más de 84 caracteres o 7 s: corte en el mejor límite de las últimas 6
   palabras, en el tiempo real de la palabra.
5. Bordes al inicio y fin de la voz cercanos (±0,35 s) y reglas de lectura: mínimo 0,9 s, ≤ 17 caracteres/s alargando
   el final en el silencio siguiente, 80 ms entre subtítulos, sin solaparse.

Medido con `tests/test_asr_timing_real.py` (cuatro frases de espeak-ng en posiciones conocidas; inicio real = primera
muestra > 300): con Silero, inicio medio 27 ms (máx. 40) y fin medio 78 ms; sin VAD (DTW + energía), 4 ms y 35 ms. Antes
de H16 whisper devolvía un único segmento de 0,8 a 10,3 s que juntaba tres frases. Tiempo: base 5,9 s (VAD) / 7,6 s
(DTW) y small-q8_0 16,6 s / 18,2 s para 16,6 s de audio con el portátil ocupado.

