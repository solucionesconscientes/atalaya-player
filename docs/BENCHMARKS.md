# Benchmarks de ASR (whisper.cpp)

Generado por `tools/bench_asr.sh` el 2026-09-29 en `pc-latitude5480`: Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz, 4 núcleos, 23 GB RAM, sin GPU dedicada.
whisper.cpp: whisper.cpp version: 1.9.3-dev · hilos: 3 · muestras: voz sintética espeak-ng (tests/fixtures/media).

RTF = tiempo de proceso / duración del audio (menor es mejor). "Palabras" = palabras clave del manifiesto reconocidas (de 7).
El coste por llamada tiene una parte fija (encoder sobre la ventana de 30 s), por eso el RTF mejora con trozos más largos.

| Modelo | Tamaño | es 12 s, -l es | es 12 s, -l es + VAD | mezcla 33.8 s, -l auto + VAD | Palabras (12 s) | Idioma detectado (mezcla) |
|---|---|---|---|---|---|---|
| base | 142 MB | 4.36 s (RTF 0.37) | 4.44 s (RTF 0.38) | 8.87 s (RTF 0.26) | 6/7 | en |
| base-q5_1 | 57 MB | 4.83 s (RTF 0.41) | 5.04 s (RTF 0.43) | 10.78 s (RTF 0.32) | 5/7 | en |
| small | 466 MB | 14.17 s (RTF 1.21) | 13.64 s (RTF 1.17) | 87.03 s (RTF 2.57) | 6/7 | la |
| small-q5_1 | 182 MB | 15.27 s (RTF 1.31) | 15.74 s (RTF 1.35) | 53.71 s (RTF 1.59) | 7/7 | la |
| small-q8_0 | 253 MB | 11.44 s (RTF 0.98) | 11.63 s (RTF 0.99) | 79.87 s (RTF 2.36) | 7/7 | la |
| tiny | 75 MB | 2.39 s (RTF 0.20) | 2.51 s (RTF 0.21) | 8.30 s (RTF 0.25) | 4/7 | en |
| tiny-q5_1 | 31 MB | 7.51 s (RTF 0.64) | 3.24 s (RTF 0.28) | 35.71 s (RTF 1.06) | 2/7 | th |

## Lectura
- En vivo (mientras mpv decodifica) se exige RTF ≤ 0,5 con trozos de 20 s; para pre-subtitular (baja prioridad) basta RTF < 1.
- Con voz sintética el modelo tiny confunde el idioma en modo `auto`; con el idioma fijado acierta. base ya reconoce casi todas las
  palabras clave; small es claramente mejor pero solo cabe en vivo con más núcleos o GPU.
- El FLAC leído directamente por whisper-cli (miniaudio) devolvió vacío en esta versión: mpvd siempre pasa WAV 16 kHz mono extraído con ffmpeg.
- Los cuantizados q5_1 son MÁS lentos que q8_0 y que el modelo completo en esta CPU (sin AVX-512); solo ahorran disco.
  (El 7,51 s de tiny-q5_1 sin VAD coincidió con otro proceso de tests en paralelo: valor contaminado.)
- Tabla de elección por tier (mpvd/asr/models.py): small (≤4 núcleos) → en vivo base, pre-cálculo base · medium → base / small-q8_0 ·
  large → small-q8_0 / small. El usuario puede forzar otro modelo desde el menú Subtítulos IA → Modelo.
