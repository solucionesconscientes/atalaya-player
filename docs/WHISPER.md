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
| `--prompt` | contexto inicial (reservado) |
| `-bs N` / `-bo N` / `-nf` | beam search / best-of / sin fallback de temperatura (`MPV_UOS_ASR_BEAM`, `MPV_UOS_ASR_BEST_OF`) |
| `--vad --vad-model FNAME` | VAD Silero v5.1.2 (`ggml-silero-v5.1.2.bin`; `MPV_UOS_ASR_VAD=0` lo desactiva) |

JSON: `{"result":{"language":"es"},"transcription":[{"timestamps":{"from":"00:00:00,030","to":"…"},"offsets":{"from":30,"to":4200},"text":" …"}]}`
(`offsets` en ms; con VAD los tiempos ya están reproyectados al audio original). `mpvd/asr/engine.py::parse_cli_json`.

## Hallazgos
- **Coste fijo por llamada**: whisper procesa siempre una ventana de 30 s, así que 1 s de silencio cuesta lo mismo que 20 s de voz
  (tiny 1,3 s · base 3,0 s · small-q5_1 12 s con 3 hilos en un i5 de 4 núcleos). Por eso los trozos son de 20 s (ADR-024) y no
  compensa `whisper-server`.
- **FLAC directo**: `whisper-cli -f voz.flac` (decodificado por miniaudio) devolvió una transcripción vacía; con WAV funciona.
  mpvd siempre extrae WAV con ffmpeg (además así selecciona la pista de audio y la ventana temporal).
- **Voz sintética** (espeak-ng): tiny en modo `auto` detecta italiano; con el idioma fijado acierta. Los tests usan `base` si está
  vendorizado y exigen ≥3 palabras clave (≥2 con tiny).
- **VAD** agrupa la voz continua en un solo segmento largo; `mpvd/asr/srt.py::split_long` lo reparte en cues legibles (≤7 s, ≤84
  caracteres).
- `whisper-server` (mismo build) admite `--vad`, `--convert` (necesita ffmpeg) y `--inference-path`; no se usa (ADR-024).

## Modelos
Catálogo y URLs oficiales (Hugging Face `ggerganov/whisper.cpp` y `ggml-org/whisper-vad`) en `mpvd/asr/models.py`; descarga bajo
demanda con verificación de la magia `lmgg` y escritura atómica. Elección por tier de hardware (`mpvd/hardware.py`) según
docs/BENCHMARKS.md. Menú "Subtítulos IA → Modelo" para descargar/borrar. `tools/bench_asr.sh` regenera los benchmarks.

## Servicio `asr.*` (mpvd)
`asr.status [id]` · `asr.models` · `asr.models.download name [notify]` · `asr.models.remove name` · `asr.start path [language model
time_pos audio_track translate chunk_seconds purpose notify]` · `asr.precompute path […]` · `asr.seek id time_pos` · `asr.stop id` ·
`asr.segments id [start end]`. Eventos push a `notify` (`script-message-to <script> mu-event <json>`): `{"event":"asr","task":{…}}`
y `{"event":"asr-model","model":…,"job":{…}}`. El SRT vive en `<cache>/asr/<hash>/<modelo>.<idioma>.srt` (escritura atómica) y el
estado (cues + trozos hechos) en la caché de artefactos, por lo que una tarea se reanuda al instante tras cerrar mpv o mpvd.
