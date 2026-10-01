# Benchmarks de ASR (whisper.cpp)

Generado por `tools/bench_asr.sh` el 2026-10-01 en `pc-latitude5480`: Intel(R) Core(TM) i5-6200U CPU @ 2.30GHz, 4 núcleos, 23 GB RAM, sin GPU dedicada.
whisper.cpp: whisper.cpp version: 1.9.3-dev · hilos: 3 · muestras: voz sintética espeak-ng (tests/fixtures/media).

RTF = tiempo de proceso / duración del audio (menor es mejor). "Palabras" = palabras clave del manifiesto reconocidas (de 7).
El coste por llamada tiene una parte fija (encoder sobre la ventana de 30 s), por eso el RTF mejora con trozos más largos.

| Modelo | Tamaño | es 12 s, -l es | es 12 s, -l es + VAD | mezcla 33.8 s, -l auto + VAD | Palabras (12 s) | Idioma detectado (mezcla) |
|---|---|---|---|---|---|---|
| medium-q5_0 | 515 MB | 47.40 s (RTF 4.05) | 47.13 s (RTF 4.03) | 138.99 s (RTF 4.11) | 7/7 | en |
| large-v3-turbo-q5_0 | 548 MB | 61.58 s (RTF 5.26) | 61.08 s (RTF 5.22) | 182.27 s (RTF 5.39) | 7/7 | es |
| small-q8_0 | 253 MB | 11.43 s (RTF 0.98) | 10.89 s (RTF 0.93) | 78.94 s (RTF 2.34) | 7/7 | la |
| base | 142 MB | 4.11 s (RTF 0.35) | 4.56 s (RTF 0.39) | 8.83 s (RTF 0.26) | 6/7 | en |

## Lectura
- Ya no se transcribe en vivo (ADR-070): se prepara el archivo entero antes de verlo, así que lo que importa es
  si el RTF baja de 1 (más rápido que el vídeo) y, si no baja, cuánto hay que esperar en total.
- Con voz sintética el modelo tiny confunde el idioma en modo `auto`; con el idioma fijado acierta. base ya reconoce casi todas las
  palabras clave; small es claramente mejor y es el que cabe en un portátil de 4 núcleos.
- El FLAC leído directamente por whisper-cli (miniaudio) devolvió vacío en esta versión: mpvd siempre pasa WAV 16 kHz mono extraído con ffmpeg.
- Los cuantizados q5_1 son MÁS lentos que q8_0 y que el modelo completo en esta CPU (sin AVX-512); solo ahorran disco.
- Tabla de elección por tier (mpvd/asr/models.py): small (≤4 núcleos) → small-q8_0, en segundo plano small-q8_0 · medium → medium-q5_0,
  en segundo plano small-q8_0 · large → large-v3-turbo-q5_0, en segundo plano medium-q5_0.
  El usuario puede forzar otro modelo desde el menú Subtítulos IA → Modelo.

<!-- escrito a mano: se conserva al regenerar -->
## Trozos de 28,5 s (2026-09-30, medida corta, no regenerada por bench_asr.sh)
Una llamada de whisper cuesta casi lo mismo con 20 s que con 28,5 s de audio (la ventana es siempre de 30 s). `voz_es.flac` en bucle,
`-l es` + VAD, 3 hilos, una pasada por caso, con otros procesos de tests en la máquina (carga media 1,7–2,8: los tiempos absolutos
salen inflados; la comparación entre filas es lo que vale):

| Modelo | Trozo 20 s (+1,2 s) | Trozo 28,5 s (+1,2 s) | RTF por segundo útil |
|---|---|---|---|
| base | 8,3 s | 9,7 s | 0,41 → 0,34 |
| small-q8_0 | 18,3 s | 16,5 s | 0,91 → 0,58 |

Con la máquina en reposo la tabla de arriba da ≈0,45 para small-q8_0 con trozos de 28,5 s. Suficiente para pre-subtitular (RTF < 1);
small-q8_0 transcribe casi perfecto y con puntuación el diálogo del diagnóstico (`tmp/diag-subs/asr`), mientras que base se
equivoca y apenas puntúa, que es lo que más estropea las traducciones.

## Por qué medium y large-v3-turbo no son el modelo por defecto (2026-10-01)
Medidos aquí por primera vez: `medium-q5_0` da **RTF 4,03** y `large-v3-turbo-q5_0` **RTF 5,22**, o sea cuatro y cinco veces más
lento que el propio vídeo (una película de 2 h: 8 h y 10 h 30 de CPU). Los tres modelos buenos aciertan 7/7 palabras clave en la
muestra, que se satura: no se puede demostrar que transcriban mejor que `small-q8_0`, pero sí que tardan 4-5 veces más. De ahí la
decisión de H36: `small-q8_0` por defecto y esos dos como «máxima calidad» explícita con el tiempo calculado delante. Único punto
a favor del turbo: fue el único que acertó el idioma (`es`) en la muestra mezclada con `-l auto`.
