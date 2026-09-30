# PROGRESS
ESTADO_GLOBAL: EN_CURSO

## Resumen para Ser (2026-09-29)
Todos los hitos H0–H13 de BACKLOG.md están [x]; ninguno quedó [~]. `tools/check.sh` pasa 202 tests sin red + 4 con red; lo único
sensible es la CPU: los tests de Whisper (`test_asr_engine`, `test_mu_subs`) fallan por tiempo si el portátil está ocupado con otros
trabajos (ver "Qué quedó pendiente").

### Qué funciona
- **Reproductor**: mpv 0.41 + uosc 5.13 con configuración portable (`mpv-config/`, nunca `~/.config/mpv`), menú MPV-UOS (`alt+m`,
  botón derecho), paleta global (`alt+p`), pantalla de inicio y "continuar viendo" por hash del contenido.
- **mpvd** (daemon Python sin dependencias obligatorias): JSON-RPC 2.0 con `capabilities`, sesiones por IPC, caché SQLite por hash,
  cola con prioridades y guardián de rendimiento; lo arranca mu-core solo.
- **TV y radio**: TDTChannels, iptv-org, Radio Browser y M3U propias; búsqueda sin acentos, favoritos, recientes, zapping, ICY y grabación.
- **yt-dlp**: binario vendorizado con actualización diaria verificada, vídeo/solo audio en caliente, menú de todos los formatos,
  descargas con presets (vídeo, audio original, MP3/Opus/M4A/FLAC/WAV con bitrate), cola con progreso.
- **Subtítulos IA** en vivo (whisper.cpp, look-ahead, caché reanudable, pre-subtitulado del siguiente), resincronización, traducción
  offline (Argos/CTranslate2) y subtítulos duales.
- **Sonido e imagen**: diálogo claro, modo noche, RNNoise, binaural, fotosensible, perfil ligero, diagnóstico de tirones.
- **MCP** para asistentes (Claude Code), **intro/créditos** por huellas de audio, **búsqueda semántica** y capítulos por tema,
  **modo estudio** (repetir línea, velocidad inteligente, notas Markdown, clips/GIF) y **mando QR/PWA** desde el móvil.
- **Instalación de usuario** en Linux sin sudo (`tools/install.sh`), guía `docs/USO.md`, teclas `docs/ATAJOS.md`, plataformas
  `docs/PLATAFORMAS.md`, decisiones ADR-001…032 en `docs/DECISIONS.md`.

### Cómo probarlo (comandos exactos)
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/check.sh                                   # todo (genera tests/fixtures/media); mejor con el portátil libre (≈7 min)
tools/install.sh --extras                        # instala mpv-uos en ~/.local/bin y "MPV-UOS" en el menú de aplicaciones
mpv-uos                                          # pantalla de inicio · alt+m menú · alt+p paleta
mpv-uos tests/fixtures/media/voz_es_en.mkv       # alt+c subtítulos IA · alt+i menú de subtítulos · alt+e estudio
mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+a solo audio · alt+q calidad · alt+d descargar · alt+l descargas
mpv-uos                                          # alt+t TV y radio · alt+f buscar canal · alt+↑/↓ zapping · alt+r grabar
mpv-uos tests/fixtures/media/serie/ep02.mkv      # intro detectada: alt+k salta · alt+j menú
mpv-uos tests/fixtures/media/chapters.mkv        # alt+z QR para el móvil (antes: sudo ufw allow … 8790, ver NEEDS_HUMAN.md)
.venv/bin/python -m mpvd status                  # estado del daemon
```
Cada hito tiene sus pasos a mano detallados más abajo, en "Registro por iteración".

### Qué quedó pendiente o bloqueado (y por qué)
- **Cortafuegos** (NEEDS_HUMAN.md): `ufw` bloquea la entrada, así que el móvil no llega al mando hasta ejecutar el `sudo ufw allow …`.
- **Notion**: en esta sesión el conector de Notion no estaba autorizado (hay que autorizarlo en los ajustes de conectores de claude.ai),
  así que la Bitácora no recoge H12–H13; las etiquetas de Stack siguen pendientes (NEEDS_HUMAN.md).
- **Windows**: falta el lanzador PowerShell y el transporte de mpvd por named pipe; **macOS** sin probar (tabla en docs/PLATAFORMAS.md).
- **Subtítulos IA en URLs/directos**: solo archivos locales (grabar el audio de la URL con ffmpeg está por hacer).
- **Tests de Whisper y carga**: con otros procesos pesados en el portátil (load > 8), `whisper-cli` va hasta 45× más lento y esos dos
  tests fallan por tiempo; con la máquina libre pasan. Repetir: `MU_KEEP_LOGS=1 uv run pytest tests/test_asr_engine.py tests/test_mu_subs.py`.
- **Ideas siguientes** del TOP 10 de docs/VISION.md aún sin hito: "¿qué me he perdido?" (B11), OCR de subtítulos PGS (B6),
  diccionario/Anki (C2–C3), handoff entre dispositivos (E5), MPRIS/KDE Connect (E4), supercut y resumen elástico (I1, I5).

## SIGUIENTE PASO
H15 · Interfaz y navegación (plan aprobado por Ser el 2026-09-30: H15–H28 en BACKLOG.md; H26 torrents pendiente de decisión).
H14 cerrado (2026-09-30). Pendiente de Ser: abrir el puerto del mando en ufw y probar el desentrelazado con 7TV (NEEDS_HUMAN.md).
Siguiente tanda posible: B11 «¿qué me he perdido?», B6 OCR de subtítulos, C2–C3 diccionario/Anki, E4 MPRIS, I1/I5.
Backlog completo. Si se reanuda: (1) Ser abre el puerto del mando y autoriza Notion (NEEDS_HUMAN.md) y ejecuta `/registrar`;
(2) nuevos hitos a partir del TOP 10 de docs/VISION.md, empezando por B11 "¿qué me he perdido?" (resumen extractivo de la
transcripción entre dos tiempos con los embeddings de H10) y E4 MPRIS (script mpv-mpris o DBus desde mpvd); (3) Windows: transporte
named pipe en mpvd (`server.py`, `client.py`, `mpvipc.py`) + `bin/mpv-uos.ps1`.

## Registro por iteración
### Iteración 5 · 2026-09-29
#### H13 · Cierre — hecho (commit "H13: cierre")
- `tools/install.sh` (lanzador `~/.local/bin/mpv-uos` → checkout, `.desktop` validado con tipos MIME y `--wayland-app-id=mpv-uos`,
  icono SVG, `--xdg`, `--extras`, `--default`, `--dry-run`, `--uninstall` que solo borra lo que lleva su marca; se niega a
  sobrescribir un `mpv-uos` ajeno). Tests (tests/test_install.py) en prefijos de `tmp/`: instalación, idempotencia, `--version` a
  través del lanzador, variables XDG, desinstalación selectiva y dry-run. No se ha ejecutado contra el `~/.local` real.
- `docs/USO.md` (guía por tareas), README (instalación, mando, guía), `docs/PLATAFORMAS.md` (tabla por componente, macOS/Windows).
- Probar a mano:
  ```bash
  tools/install.sh --dry-run          # qué haría
  tools/install.sh && mpv-uos --version && tools/install.sh --uninstall
  ```
#### H13 · Cierre — plan
- `tools/install.sh`: instalación de usuario sin sudo (lanzador en `~/.local/bin` que apunta al checkout, `.desktop` validado con
  tipos MIME, icono SVG, `--xdg` para caché/datos en rutas XDG, `--default` con xdg-mime, `--dry-run`, `--uninstall` que solo borra
  lo que lleva su marca). Tests en prefijos de `tmp/` (nunca el `~/.local` real).
- `docs/USO.md` (guía por tareas), README (instalación, mando, enlace a la guía), `docs/PLATAFORMAS.md` (tabla por componente,
  instalación macOS/Windows), resumen final y `ESTADO_GLOBAL: COMPLETADO` en PROGRESS.md.
#### H12 · Mando QR/PWA — hecho (commit "H12: mando QR/PWA")
- Verificación real (subagente → docs/REMOTE_API.md): `overlay-add` exige fichero BGRA y coordenadas de pantalla → QR como overlay ASS
  (`mp.create_osd_overlay`); ni `qrencode` ni `segno` instalados → QR en Python puro con tablas copiadas de una implementación de
  referencia; `ufw` activo con entrada DROP (ver NEEDS_HUMAN.md).
- `mpvd/remote/`: `qr.py` (modo byte, v1–10, nivel M, 8 máscaras), `http.py` (HTTP/1.1 + SSE sobre asyncio), `service.py`
  (`remote.status/start/stop/pair/forget`; token de un solo uso de 10 min en `#t=` → cookie HMAC; lista blanca de órdenes; estado
  por SSE ≤2 Hz; móviles emparejados en `<datos>/remote.json`), `www/` (PWA: control, canales, búsqueda, recientes, más). ADR-032.
- `mu-remote/main.lua`: `alt+z` QR + URL (se oculta a los 120 s), `alt+Z` menú (estado, móviles, olvidar, arrancar/detener), entrada
  en el menú raíz; estado en `user-data/mu/remote`. `SessionManager` avisa a oyentes al abrir/cerrar sesiones (el mando sigue al mpv
  más reciente si se cierra el que mostró el QR).
- Tests (tests/test_remote.py, 17): QR decodificado con zbarimg en todas las versiones y máscaras, servidor HTTP y SSE, emparejamiento
  (token inválido/reutilizado rechazado, cookie falsa rechazada, Origin ajeno rechazado), órdenes aplicadas en mpv headless, SSE con
  time-pos, persistencia y desemparejar, mu-remote headless (overlay y menú). Docs: docs/REMOTE.md.
- Probar a mano (con el puerto abierto en ufw):
  ```bash
  bin/mpv-uos tests/fixtures/media/chapters.mkv     # alt+z → escanear el QR con el móvil (misma wifi); alt+Z menú
  .venv/bin/python -m mpvd call remote.status
  curl -s http://127.0.0.1:8790/api/state            # sin emparejar → 401
  ```
### Iteración 4 · 2026-09-29
#### H11 · Estudio — hecho (commit "H11: modo estudio")
- Verificación real (subagente → docs/ESTUDIO.md): sub-start/sub-end sin sub-delay y `unavailable` sin cue; `ab-loop-a` = "no";
  speed en caliente sin errores; ffmpeg copy corta en keyframe; silencedetect -30 dB/0,3–0,5 s para pausas de voz; VAD Silero disponible.
- `mpvd/study/`: `clips.py` (argv por formato mp4/mp4-copy/mkv-copy/gif/mp3/opus/wav, `-progress pipe:1`, nombres
  `<stem> [hh.mm.ss-hh.mm.ss].ext`), `silence.py` (mapa de silencios con padding adaptativo), `service.py` (`study.formats`,
  `study.clip` job con eventos `clip` a todas las sesiones e historial `clips.json`, `study.clips.list/cancel`, `study.silences`
  cacheado por hash). ADR-031.
- `mu-study/main.lua`: alt+e menú Estudio; alt+w repetir línea (alt+←/→ anterior/siguiente; se apaga si el usuario quita el A-B);
  alt+g velocidad inteligente (opciones `mu-study-silence_speed/silence_db/silence_min`); alt+b nota (cuadro de búsqueda de uosc
  como campo de texto + cita del subtítulo → notes.add); alt+u clip del A-B o de la línea (formato por menú; abrir el clip
  terminado desde el menú). Estado en `user-data/mu/study`; mensajes `mu-study-note/clip/smart/repeat`.
- Tests (tests/test_study.py): argv y nombres, 5 formatos reales con ffprobe, métodos vía daemon (cola, historial, errores,
  silencios cacheados), mu-study headless (bucle con sub-delay, apagado al quitar A-B, nota con cita y enlace, clip mp3 del A-B
  con evento, velocidad ×2,5 en pausa y vuelta a ×1,25, menú).
- Probar a mano:
  ```bash
  bin/mpv-uos pelicula.mkv    # con subtítulos: alt+w repite la línea; alt+g acelera silencios; alt+b nota; l l marca A-B y alt+u exporta
  .venv/bin/python -m mpvd call study.clips.list
  cat "$(.venv/bin/python -m mpvd call notes.list | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["file"])')"
  ```
#### H10 · Búsqueda semántica y capítulos automáticos — hecho (commit "H9+H10")
- `mpvd/semantic/`: `embed.py` (Embedder ONNX + sentencepiece, 2 hilos, descarga verificada del modelo a vendor/models/embed),
  `index.py` (frases desde cues, blob float32, búsqueda coseno + bonus literal, capítulos por cambio de tema, ffmetadata),
  `service.py` (`semantic.status/models.download/index/search/chapters`; caché por hash; fallback literal; indexado en segundo
  plano), `fake.py` (embedder de test). `asr.inject` (gancho de test). Extra `semantic` en pyproject; check.sh lo instala.
- mu-menu: sección "Diálogo (semántico)" en la paleta (Enter → seek). mu-subs: "Capítulos por tema (IA)" (aplica/quita
  `chapter-list`; opciones `mu-subs-chapter_min_seconds`, `mu-subs-chapter_window`; mensaje `mu-subs-chapters yes|no`).
- docs/SEMANTICA.md (verificación real: paquetes, modelos HF con SHA, benchmark), ADR-030, README, ATAJOS.
- Tests (tests/test_semantic.py): unión de frases, búsqueda y blob, capítulos (3 temas → 3 capítulos, homogéneo → 0),
  modelo real ES↔EN (se omite sin extra/modelo), métodos vía daemon con embedder falso, paleta + capítulos en mpv headless.
- Probar a mano:
  ```bash
  bin/mpv-uos charla.mkv        # alt+c subtítulos IA hasta el final; alt+p y escribe "cuando hablan de X" → sección Diálogo
                                # alt+i → "Capítulos por tema (IA)" → capítulos en la barra de uosc
  .venv/bin/python -m mpvd call semantic.chapters '{"path":"'$PWD'/charla.mkv"}'
  ```
#### H10 · Búsqueda semántica y capítulos automáticos — plan
- Verificado (subagente → docs/SEMANTICA.md): `onnxruntime` 1.30 (wheel cp312 23,6 MB; numpy, flatbuffers, protobuf, packaging) +
  `sentencepiece` (ya en `translate`) bastan; `tokenizers` arrastra huggingface-hub y 270 MB de RSS, se descarta. Modelo
  `Xenova/paraphrase-multilingual-MiniLM-L12-v2` `onnx/model_quantized.onnx` (118 MB, u8u8: 2× más rápido que el quint8_avx2 en
  esta CPU sin VNNI) + `sentencepiece.bpe.model` (5 MB) del repo sentence-transformers; dim 384, mean pooling + L2, max 128 tokens,
  sin prefijos; medido: carga 1,4 s, ~6 ms por segmento Whisper con 2 hilos, RSS 270 MB; coseno ES↔EN 0,98/0,86 vs <0,09 no
  relacionadas. sqlite-vec funciona pero no hace falta (<2 000 vectores por archivo → NumPy). `chapter-list` de mpv 0.41 es
  escribible por IPC (probado: set_property chapter-list [{title,time}]).
- mpvd `semantic/`: `embed.py` (Embedder ONNX+spm portado del benchmark, hilos = min(2, nproc-1), descarga de modelo con SHA-256 como
  av.py, `available()`), `index.py` (frases = segmentos Whisper agrupados hasta ~25 palabras; vectores float32 en blob de la caché por
  (hash, "embed", modelo, versión, params); búsqueda coseno + fusión con coincidencia textual; capítulos: ventanas 45 s solape 50 %,
  d=1−cos, media móvil 3, máximos > percentil 85, mínimo 180 s, título = frase más cercana al centroide), `service.py`
  (`semantic.status`, `semantic.models.download`, `semantic.index {path}` job PRECOMPUTE, `semantic.search {q, path?, k}`,
  `semantic.chapters {path, min_seconds?, percentile?}` con caché y exportación ffmetadata).
- mu-menu paleta: sección "Diálogo" (`semantic.search` del archivo actual; si no hay índice cae a `asr.search`); Enter → seek.
  mu-subs: entrada "Capítulos por tema (IA)" → `semantic.chapters` → `chapter-list`; "Quitar capítulos IA" restaura los originales.
- Tests: embedder falso determinista (bolsa de palabras → vector) para índice/búsqueda/capítulos; test con modelo real si está en
  vendor/models/embed y onnxruntime importable (búsqueda cruzada ES/EN sobre voz_es/voz_en); integración headless (paleta "Diálogo",
  capítulos aplicados en `chapter-list`).
#### H9 · Salto de intro/créditos — hecho (commit "H9+H10")
- `mpvd/intro/`: `fingerprint.py` (huella `fpcalc -raw -json` por ventana sobre WAV extraído con ffmpeg; `match()` = votos por
  diagonal + rachas Hamming ≤6 con recorte de bordes planos), `detect.py` (`silencedetect`/`blackdetect` de ffmpeg → cortes y `snap`),
  `service.py` (`intro.segments` cache→análisis en segundo plano con evento push, `intro.analyze` (`wait`), `intro.export`;
  vecinos por número de episodio (S01E02, 1x02, ep02, 02), consenso por mediana, exportación a `<carpeta>/.mpv-uos/segments.json`).
  `capabilities.services.intro` = hay `fpcalc`. ADR-029.
- `mu-intro/main.lua`: pide segmentos en file-loaded, sondeo de time-pos (0,5 s) → botón `mu-skip` en la barra de uosc con badge
  intro/fin + aviso OSD; `alt+k` salta (intro → fin del tramo; créditos → siguiente elemento de la lista o final; fuera de tramo →
  fin del siguiente); `alt+j` menú (segmentos con tiempos, saltar ahora, salto automático de intro/créditos, activar/desactivar,
  volver a analizar); mensajes `mu-intro-skip <tipo>`, `mu-intro-set <clave> yes|no`, `mu-intro-refresh`; estado en
  `user-data/mu/intro`. Entrada "Saltar intro y créditos" en el menú raíz; opciones `mu-intro-*` (auto_skip_intro/credits, enabled).
- tools/make_test_media.sh genera `serie/ep01..03.mkv` (38 s: 1,5 s negro, intro común de 8 s, cuerpo distinto de 22 s,
  0,5 s de silencio, créditos comunes de 6 s) con segmentos esperados en manifest.json.
- Tests (tests/test_intro.py, se omiten sin fpcalc): vecinos/snap, huellas ep01↔ep02 (intro y créditos encontrados, cuerpos no
  coinciden), servicio (análisis → caché → segments.json → intro.export, segundo episodio rápido por caché), mu-intro headless
  (segmentos, menú, salto en la intro, créditos → siguiente episodio, salto automático).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/serie/ep01.mkv tests/fixtures/media/serie/ep02.mkv   # a los ~2 s: "Intro · alt+k"; alt+j menú
  .venv/bin/python -m mpvd call intro.analyze '{"path":"'$PWD'/tests/fixtures/media/serie/ep02.mkv","wait":true}'
  cat tests/fixtures/media/serie/.mpv-uos/segments.json
  ```
### Iteración 3 · 2026-09-29
#### H8 · MCP rico — hecho (commit "H8: MCP")
- `mpvd/mcp.py`: servidor MCP stdio sin dependencias (ADR-028): initialize/ping/tools/resources/prompts; 11 tools (status, play,
  pause, resume, seek, search_dialogue, list_channels, play_channel, download, add_note, subtitles_ai) y resources
  `mpv://transcript/<id>` y `mpv://notes/<clave>`; errores de tool como `isError`. CLI `python -m mpvd mcp [--session] [--yes]`
  (arranca el daemon si hace falta; logs por stderr).
- mpvd: `mpvd/control.py` (`session.get/set/command/confirm`, `notes.add/list/read` con enlaces `mpv://seek?t=`), `asr.search`,
  `sessions.all()`. mu-menu: diálogo `mu-confirm <token> <texto>` (uosc Sí/No, 15 s, publica `user-data/mu/confirm{_request}`).
- `.mcp.json.example`, docs/MCP.md, README.
- Tests: test_mcp (handshake, tools/list, status idle, play/pause/resume/seek autoconfirmados, append, notas + resource, errores,
  search_dialogue sin transcripción lanza una; diálogo real de confirmación: sí → salta, no → no salta).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv &
  cp .mcp.json.example .mcp.json   # y en Claude Code: /mcp → mpv-uos → status, "salta al minuto 0:20" (confirma en la pantalla de mpv)
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"sh","version":"0"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"status","arguments":{}}}' | .venv/bin/python -m mpvd mcp
  ```
#### H8 · MCP rico — plan
- Verificado: el SDK oficial `mcp` 2.2.0 arrastra ~30 paquetes (pydantic, starlette, uvicorn, cryptography, opentelemetry…): se
  descarta (ADR-028). Se implementa el protocolo MCP mínimo a mano (JSON-RPC 2.0 por stdio, una línea por mensaje; spec 2025-06-18:
  initialize, notifications/initialized, ping, tools/list, tools/call, resources/list, resources/read, prompts/list vacío).
- mpvd: métodos nuevos `session.get/set/command` (control de una sesión de mpv por su id o la actual), `asr.search` (búsqueda sin
  acentos en los segmentos transcritos), `notes.add/list` (Markdown en `<data_dir>/notas/<clave>.md` con enlaces de tiempo).
  mu-menu: `mu-confirm <token> <texto>` abre un diálogo uosc Sí/No y publica `user-data/mu/confirm = {token, answer}`.
- `python -m mpvd mcp`: tools status, play, pause, resume, seek, search_dialogue, list_channels, play_channel, download, add_note;
  resources `mpv://transcript/<clave>`; confirmación en OSD (15 s) para play/seek/play_channel/download salvo `MPVD_MCP_AUTOCONFIRM=1`.
  `.mcp.json.example` + README. Tests: cliente MCP mínimo por stdio contra el daemon de test + mpv headless.
#### H7 · Sonido e imagen — hecho (commit "H7: sonido e imagen")
- `mpvd/av.py`: servicio `av.models` / `av.models.download` / `av.models.path` (RNNoise sh/bd y HRTF MIT KEMAR fijados por SHA-256 en
  vendor.lock; descarga con progreso por eventos `av-model`). Modelos ya descargados en vendor/models/{rnnoise,sofa}.
- `mu-av/main.lua`: menú "Sonido e imagen" (alt+v, botón en la barra, entrada en el menú raíz): diálogo claro, modo noche (alt+n),
  reducción de ruido (arnndn→afftdn), binaural (sofalizer→crossfeed), protección fotosensible (vf), perfil ligero (guarda/restaura),
  diagnóstico de tirones con contadores y consejos, vista de modelos con descarga, "quitar todos". Filtros etiquetados `@mu-<x>`.
- docs/AUDIO_VIDEO.md (grafos validados, propiedades, alternativas), ADR-027, README, ATAJOS.
- Tests: test_mu_av (cada filtro añade su grafo lavfi y la reproducción sigue sin errores lavfi, RNNoise/SOFA usados si están,
  toggles, perfil ligero restaura, menú raíz/diagnóstico/quitar todos), av.models y errores.
- Probar a mano:
  ```bash
  bin/mpv-uos pelicula.mkv        # alt+v → activa "Diálogo claro" y "Modo noche"; alt+n alterna noche; Diagnóstico de tirones
  .venv/bin/python -m mpvd call av.models
  ```
#### H7 · Sonido e imagen — plan
- Verificado (mpv 0.41 `--af=help`/`--vf=help` + ejecución headless con `--end=3` sin errores en el log): lavfi disponible con
  acompressor, afftdn, alimiter, anlmdn, arnndn, compand, crossfeed, deesser, dynaudnorm, equalizer, haas, highpass, loudnorm, lowpass,
  sofalizer (libmysofa compilado), speechnorm, stereotools; vídeo: photosensitivity, deband, deflicker, eq, hqdn3d, nlmeans, tmix,
  unsharp. Filtros con etiqueta `@mu-<x>:lavfi=[grafo]` (`af add/remove/toggle`). Propiedades de diagnóstico: frame-drop-count,
  decoder-frame-drop-count, mistimed-frame-count, vo-delayed-frame-count, estimated-vf-fps, container-fps, display-fps,
  estimated-display-fps, hwdec-current, current-vo, video-out-params.
- Modelos descargados y fijados por SHA-256 en vendor.lock: RNNoise `sh.rnnn` (somnolent-hogwash, general) y `bd.rnnn`
  (beguiling-drafter, voz), ~300 KB cada uno (GregorR/rnnoise-models, BSD); HRTF `mit_kemar_normal_pinna.sofa` (1,1 MB, sofacoustics.org).
- Grafos: diálogo claro `highpass=f=70,dynaudnorm=f=250:g=11:p=0.85:m=8,equalizer=f=2800:t=q:w=1.2:g=2.5`; modo noche
  `acompressor=threshold=-24dB:ratio=6:attack=5:release=400:makeup=4dB,alimiter=limit=0.7`; ruido `arnndn=m=<rnnn>:mix=0.9`
  (sin modelo: `afftdn=nr=12:nf=-40`); binaural `sofalizer=sofa=<sofa>:type=freq` (sin SOFA: `crossfeed=strength=0.5:range=0.5`);
  fotosensible `vf @mu-photo:lavfi=[photosensitivity=frames=30:threshold=1:bypass=0]`.
- Pasos: `mpvd/av.py` (`av.models`, `av.models.download` con eventos `av-model`) → `mu-av/main.lua` (menú "Sonido e imagen" alt+v,
  modo noche alt+n, toggles por etiqueta, diagnóstico de tirones con recomendaciones y "perfil ligero") → tests headless (cada filtro
  activo en `af`/`vf`, reproducción sigue, sin errores; diagnóstico; perfil ligero) → docs/AUDIO_VIDEO.md, ADR-027, README, ATAJOS.
#### H6 · Sincronía, traducción y duales — hecho (commit "H6: sincronía, traducción y duales")
- `mpvd/subs/formats.py` (SRT/VTT/ASS, BOM/utf-8/utf-16/cp1252, etiquetas fuera), `resync.py` (ADR-026: emparejamiento por palabras en
  banda ±120 s, cadena monótona de máximo peso, recta robusta Theil–Sen por ventana con cortes y extrapolación), `translate.py`
  (ADR-025: paquetes Argos sobre CTranslate2 + sentencepiece, pivote por inglés, agrupación de cues sin tocar tiempos, descarga con
  SHA-256 e índice oficial cacheado), `service.py` (`subs.info/shift/resync/translate/translate.models/translate.download/translate.remove`,
  eventos push, caché de traducciones por hash del SRT).
- mu-subs: "Resincronizar la pista externa con la IA" (alt+x; si no hay transcripción la lanza y reintenta al terminar),
  "Traducir la pista seleccionada a…" (paquete que falte → se descarga y se traduce solo), "Duales: original arriba + traducción abajo"
  (`secondary-sid`), pista "Traducción (xx)"; `sub-reload` solo mientras la pista IA está seleccionada (no roba la selección).
- pyproject: extra opcional `translate` (ctranslate2 4.8, sentencepiece 0.2; .venv ≈206 MB); check.sh lo instala si falta;
  vendor.lock: URLs + SHA-256 de los paquetes es→en / en→es 1.0. Modelos en vendor/models/argos/{es_en,en_es} (ignorado por git).
- Tests: test_subs_resync (retraso, deriva 4 % con ruido, corte de 12 s, sin coincidencias, formatos/codificaciones),
  test_subs_translate (segmentación, agrupación/reparto, store/zip sintético, traducción real es→en ≥5 palabras clave),
  test_subs_service (resync pending→done con desfase −2,5 s recuperado; traducción job→SRT inglés, caché, errores),
  test_mu_subs ampliado (resync desde el menú → pista "Resincronizado", traducción → pista "Traducción (en)", duales con
  `secondary-sub-text` y `sub-text`).
- Probar a mano:
  ```bash
  uv sync --extra translate
  bin/mpv-uos pelicula.mkv                 # alt+c (subtítulos IA) · alt+s carga un .srt externo → alt+x lo resincroniza
                                           # alt+i → Traducir la pista seleccionada a… → Inglés; luego Duales
  .venv/bin/python -m mpvd call subs.translate.models
  .venv/bin/python -m mpvd call subs.resync '{"path":"pelicula.mkv","srt":"pelicula.srt","language":"es"}'
  ```
#### H6 · Sincronía, traducción y duales — plan
- Verificado (mpv 0.41 `--list-options/--list-properties`): `secondary-sid` (no|auto|0-8190), `secondary-sub-pos` (0-150, 0 = arriba),
  `secondary-sub-delay`, `secondary-sub-visibility`, `secondary-sub-text`, `sub-delay`, `sub-speed`, `sub-pos`.
- Resync (`mpvd/subs/`): `formats.py` carga SRT/VTT/ASS (codificación con BOM/utf-8/cp1252) → cues; `resync.py` alinea las cues del
  externo con los segmentos Whisper de la tarea `asr` del archivo: similitud por Jaccard de palabras sin acentos dentro de una banda
  de ±120 s, cadena monótona de máximo peso (LIS ponderada), desfase por tramos = mediana de los pares por ventana + interpolación
  lineal entre ventanas (deriva). Servicio `subs.resync {path, srt, model?, language?}` → SRT corregido en caché + estadísticas
  (pares, desfase mediano, deriva); si no hay transcripción, lanza `asr` (precompute) y devuelve `pending` con el id de la tarea.
- Traducción: según docs/TRADUCCION.md (subagente): paquetes Argos (CTranslate2 + sentencepiece) sin argostranslate/stanza; servicio
  `subs.translate {path, srt|task, source, target}` con caché por (hash, artefacto, par, versión) → SRT traducido.
- Duales: mu-subs "Duales: original arriba + traducción abajo" = `secondary-sid` original (arriba, `secondary-sub-pos=0`... por
  defecto ya es 0) y `sid` traducción; menú Subtítulos IA → entradas "Resincronizar…", "Traducir…", "Duales".
#### H5 · Subtítulos IA en vivo — hecho (commit "H5: subtítulos IA en vivo")
- `tools/vendor_whisper.sh` copia whisper-cli/whisper-server + libs ggml/whisper + modelos desde el build de live-captions-linux
  (ADR-023); `vendor/whisper/VERSION`. `tools/bench_asr.sh` → docs/BENCHMARKS.md (i5-6200U, 3 hilos: base RTF 0,26–0,38, tiny 0,20,
  small-q8_0 ≈1,0; q5_1 más lentos que q8_0). Tablas de tier en `asr/models.py`: small/medium → base en vivo; large → small-q8_0.
- mpvd `asr/`: `audio.py` (WAV 16 kHz vía ffmpeg), `srt.py` (cues, fusión por trozos, SRT), `models.py` (catálogo, descarga bajo
  demanda con magia ggml, elección por tier), `engine.py` (whisper-cli por trozo, JSON, cerrojo global), `service.py` (tareas por
  archivo+modelo+idioma, plan de trozos de 20 s ordenado por distancia al cursor, `asr.seek` mueve el cursor, estado en caché de
  artefactos → reanudación instantánea, eventos push `asr` y `asr-model`). Métodos: asr.status/models/models.download/models.remove/
  start/precompute/seek/stop/segments. Solo archivos locales con duración (ADR-023).
- `mu-subs/main.lua`: menú "Subtítulos IA" (alt+i; botón CC): iniciar/detener (alt+c), idioma, modelo (descargar/borrar, progreso),
  activar automáticamente, pre-subtitular el siguiente de la lista, estado del motor. `sub-add` de la primera versión del SRT y
  `sub-reload` (≤1/s) en cada evento; `asr.seek` en seeks y cada 5 s; adopta desde caché la tarea pre-calculada del siguiente.
- mu-menu: entrada "Subtítulos IA (whisper)"; uosc.conf: `button:mu-subs`; input.conf + docs/ATAJOS.md; check.sh: paso whisper.
- Docs: docs/WHISPER.md (opciones reales, JSON, hallazgos: coste fijo del encoder de 30 s, FLAC vacío por miniaudio, tiny confunde
  idioma en `auto` con voz sintética), docs/BENCHMARKS.md, ADR-023/024, README, PLATAFORMAS.
- Tests: test_asr_engine (argv vs `--help` real, JSON, catálogo/tier, transcripción real de voz_es ≥3 palabras clave con base),
  test_asr_service (daemon: errores, tarea en vivo sobre la pista inglesa de voz_es_en.mkv, SRT sin solapes, reanudación desde caché
  y tras reiniciar mpvd, trozo bajo el cursor primero, precompute), test_mu_subs (mpv headless: pista externa añadida y seleccionada,
  `sub-text` muestra la cue tras un seek, precompute del siguiente adoptado al cambiar, menús root/idioma/modelos/estado, URL/idle
  rechazados con OSD). Se omiten si falta whisper (tools/vendor_whisper.sh).
- Latencia medida en tests (base, 3 hilos): primer trozo de 6 s listo en ≈4 s; trozo de 20 s en ≈5–6 s (RTF ≈0,3); mpv sin drops
  (headless). Pendiente de H5 ampliado: URLs/directos (grabar el audio desde la URL resuelta).
- Probar a mano:
  ```bash
  tools/vendor_whisper.sh && tools/bench_asr.sh          # binarios + tabla de RTF
  bin/mpv-uos tests/fixtures/media/voz_es_en.mkv          # alt+i → Iniciar (o alt+c); aparece la pista "Subtítulos IA (base · es)"
  .venv/bin/python -m mpvd call asr.status                # tareas, RTF, modelos; asr.models para descargar otros
  MPV_UOS_TEST_ASR_MODEL=tiny uv run pytest tests/test_asr_service.py tests/test_mu_subs.py -q
  ```
#### H5 · Subtítulos IA en vivo — plan
- Verificado en esta máquina (vendor/whisper/bin, libwhisper 1.9.3 de live-captions-linux): `whisper-cli --help` real
  (-m/-f/-t/-l/-oj/-of/-np/-tr/-bs/-bo/-nf/--prompt/--vad/--vad-model/-ml/-sow), JSON `transcription[].offsets{from,to}` en ms,
  `result.language`. El FLAC leído por miniaudio da vacío → siempre WAV vía ffmpeg (ya lo hace asr/audio.py). Coste fijo por
  llamada = encoder de 30 s (tiny 1.3 s, base 3.0 s, small-q5_1 12 s con 3 hilos, incluso con 1 s de audio): el modelo se carga
  rápido, así que whisper-server no aporta nada y se descarta (ADR-024); trozos de 20 s por defecto.
- mpv 0.41 (`--input-cmdlist`): `sub-add url [flags] [title] [lang]`, `sub-reload [id]`, `sub-remove [id]`. mu-subs localiza la
  pista por `external-filename` en `track-list` (el id puede cambiar al recargar) y recarga como mucho 1 vez/s.
- Pasos: tools/vendor_whisper.sh (copia binarios+libs+modelos desde live-captions-linux o $WHISPER_BUILD) → tools/bench_asr.sh →
  docs/BENCHMARKS.md (RTF por modelo/hilos/VAD) → ajustar tablas de tier en asr/models.py → repaso asr/service.py (chunk 20 s,
  progreso de descarga de modelos por push) → mu-subs/main.lua (menú "Subtítulos IA": iniciar/parar, idioma, modelo, descargar
  modelo, pre-subtitular el siguiente; sub-add/sub-reload; asr.seek en seek y cada 5 s; botón uosc con progreso; alt+i / alt+c)
  → tests (engine con tiny/base sobre voz_es/voz_en por palabras clave; servicio asr.* vía daemon con reanudación desde caché;
  mu-subs headless: pista externa añadida, cues correctas, precompute del siguiente) → docs (WHISPER.md, ATAJOS.md, ADR-023/024,
  PLATAFORMAS) → check.sh → commit.
### Iteración 1 · 2026-09-28
#### H0 · Cimientos — plan
- Entorno verificado: mpv 0.41.0 (Lua OK; vo gpu-next; hwdec vulkan/vaapi/nvdec), uv 0.12.14 con CPython 3.12.14 disponible,
  ffmpeg 8.0.1 (libx264, aac, libopus, flac, libmp3lame), espeak-ng 1.52 (voces es, en-us), luajit 2.1 + luacheck, yt-dlp 2026.08.19,
  node, jq, sqlite3, fpcalc, vulkaninfo/vainfo. Hardware: 4 núcleos, 23 GB RAM, sin GPU dedicada. Hostname pc-latitude5480.
- Terceros: uosc 5.13.0 (release 2026-08-03) y thumbfast master 0f711de (2026-06-28); hashes SHA-256 fijados en vendor.lock.
- Pasos: pyproject + uv (paquete mpvd, cliente IPC) → bin/mpv-uos → mpv-config (mpv.conf, input.conf, uosc, thumbfast, mu-core esqueleto)
  → tools/make_test_media.sh → tests pytest headless + tools/check.sh → README y docs/PLATAFORMAS.md.
#### H0 · Cimientos — hecho (commit "H0: cimientos")
- `pyproject.toml` (paquete `mpvd`, build uv_build, pytest + pytest-timeout), `.python-version`=3.12, `.venv` con `uv sync`.
- `mpvd/mpvipc.py`: cliente asyncio del JSON IPC de mpv (command/get/set, cola de eventos, wait_event, wait_property).
- `bin/mpv-uos`: lanza el mpv del sistema con `--config-dir=<proyecto>/mpv-config` y `--input-ipc-server=$XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock`;
  exporta `MPV_UOS_ROOT` y `MPV_UOS_SOCKET`.
- `mpv-config/`: mpv.conf (gpu-next, auto-safe, save-position-on-quit, osc=no, osd-bar=no…; cada clave validada por test), input.conf con
  menú uosc (`#!`), uosc 5.13.0 + thumbfast 0f711de vendorizados (`vendor.lock`, `tools/vendor.sh`, ziggy fuera de git), `mu-core.lua` esqueleto
  que publica `user-data/mu/core` y detecta uosc.
- `tools/make_test_media.sh`: video30.mkv, chapters.mkv (3 capítulos), voz_es/voz_en.flac (espeak-ng, 16 kHz mono, JSON con frases y
  palabras clave), voz_es_en.mkv (2 pistas de audio spa/eng), manifest.json.
- `tools/check.sh`: entorno → vendor → medios → luacheck → shell lint → pytest (sin red) → pytest -m network si hay conectividad.
- Tests (16): launcher, opciones de mpv.conf contra `--list-options`, input.conf sin errores, vendor.lock ↔ uosc instalado, smoke headless
  (uosc/thumbfast/mu-core cargan sin errores Lua, uosc registra bindings, reproduce capítulos y pistas), cliente IPC, medios ↔ manifest.
- Probar a mano:
  ```bash
  tools/check.sh                                   # todo en verde
  bin/mpv-uos tests/fixtures/media/chapters.mkv    # ventana con uosc en español; botón derecho = menú
  bin/mpv-uos --vo=null --ao=null --idle=yes --input-ipc-server=tmp/a.sock &   # headless
  echo '{"command":["get_property","user-data/mu/core"]}' | socat - UNIX-CONNECT:tmp/a.sock   # o con python: mpvd.mpvipc
  ```
- Pendiente de H0: registro en Notion (delegado a subagente en esta iteración).
#### H1 · mpvd núcleo — plan
- Transporte: mpv Lua no tiene sockets, así que mu-core NO mantiene conexión; lanza `python -m mpvd ensure --attach <ipc>` (subproceso corto,
  asíncrono) que arranca el daemon si no responde y registra la sesión. Después es **mpvd quien se conecta al IPC de mpv** (cliente JSON IPC)
  y desde ahí: recibe peticiones JSON-RPC que los scripts envían con `script-message mu-rpc <json> [script-destino]` (evento `client-message`),
  responde con `script-message-to <script> mu-reply <json>`, observa propiedades (frame-drop-count, pause) para el guardián y detecta el cierre.
- Módulos: config (rutas runtime/caché dev vs XDG), rpc (JSON-RPC 2.0 con batch y errores estándar), server (socket Unix, pidfile, idle-timeout),
  sessions (una por mpv), hashing (OpenSubtitles + blake2b de tamaño+64 KB inicio/fin), cache (SQLite + blobs, claves
  (hash, artefacto, modelo, versión, parámetros)), jobs (cola con prioridades URGENT/INTERACTIVE/PRECOMPUTE/INDEX, cancelación, progreso),
  guardian (tasa de frames perdidos → frena trabajos pesados), client + CLI (serve/ensure/call/status/stop).
- mu-core.lua: opciones (script-opts mu-core-*), ensure con reintentos/backoff, `mu-hello`/`mu-reply`, API `rpc(method, params, cb)`,
  watchdog (ping periódico), `mu-call` para pruebas que guarda en `user-data/mu/last_reply`, estado en `user-data/mu/core`.
- Tests: unit (rpc, hashing, cache, jobs, guardian, server in-process) + integración (mpv headless → mu-core arranca mpvd → sesión
  registrada → ping ida y vuelta → hash de archivo → shutdown del daemon → reconexión por watchdog → cierre limpio).
- Notion: ficha creada (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) y Bitácora `mpv-uos|2026-09-28|avance|h0-cimientos`.
  Slug añadido a CLAUDE.md. Detalles pendientes para Ser en NEEDS_HUMAN.md (Stack, Repo, marcador ~/.cache/notion-reg).
#### H1 · mpvd núcleo — hecho (commit "H1: mpvd núcleo")
- `mpvd/`: `rpc.py` (JSON-RPC 2.0, batch, códigos estándar), `server.py` (socket Unix 0600, pidfile, socket viejo limpiado, idle-timeout),
  `sessions.py` (una conexión IPC por mpv, hello, observe_property, mu-rpc/mu-reply), `methods.py` (ping, version, capabilities,
  shutdown, sessions.*, jobs.*, guardian.*, file.hash, cache.*), `jobs.py` (cola con prioridades urgent/interactive/precompute/index,
  cancelación, progreso, historial), `guardian.py` (tasa de frame-drop-count > 2/s → frena trabajos `heavy` 10 s), `hashing.py`,
  `cache.py` (SQLite WAL + blobs, get/put/invalidate/stats/prune LRU), `hardware.py` (tier small/medium/large), `client.py`,
  `__main__.py` (serve / ensure / call / status / stop), `config.py` (rutas dev .cache vs XDG).
- `mu-core.lua` 0.2.0: opciones `mu-core-*`, ensure con backoff, `mu-hello`, `rpc()` con timeouts, watchdog, `mu-call` (guarda en
  `user-data/mu/last_reply`), estado en `user-data/mu/core`.
- Tests (59 en total): rpc, hashing (referencia independiente), cache, jobs+guardian, server in-process, e integración real:
  mpv headless → mu-core arranca mpvd → sesión registrada con pid → ping/file.hash/sessions.current ida y vuelta → observa `path` →
  trabajo cancelado al cerrar mpv → watchdog reconecta tras `shutdown` → CLI `ensure` idempotente.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv           # mu-core arranca mpvd solo
  .venv/bin/python -m mpvd status                        # sesiones, trabajos, guardián
  .venv/bin/python -m mpvd call capabilities             # métodos disponibles
  .venv/bin/python -m mpvd call jobs.sleep '{"seconds":3,"heavy":true}' && .venv/bin/python -m mpvd call jobs.list
  .venv/bin/python -m mpvd call file.hash '{"path":"tests/fixtures/media/video30.mkv"}'
  .venv/bin/python -m mpvd stop                          # mu-core lo relanza en ≤30 s (watchdog)
  tail -f .cache/mpvd.log
  ```
- Dentro de mpv (consola `): `script-message-to mu_core mu-call ping` y leer `user-data/mu/last_reply`.
#### H2 · TV y radio — plan
- Verificado en mpv 0.41: `http-header-fields` (lista), `user-agent`, `referrer`, `tls-verify`, `stream-lavf-o`, `stream-record=<archivo>`,
  `loadfile <url> replace -1 {opciones por archivo}` (índice -1 obligatorio desde 0.38), `metadata/by-key/<k>` para el título ICY.
- Fuentes reales y fixtures: subagente descarga TDTChannels (tv/radio/tvradio), iptv-org (index + índices por país/categoría/idioma + API
  JSON) y Radio Browser; deja docs/FUENTES_IPTV.md y tests/fixtures/iptv/. Menu API de uosc 5.13 verificada en docs/UOSC_API.md.
- mpvd: `net.py` (caché HTTP con ETag/Last-Modified, TTL, offline) → `iptv/m3u.py` (parser tolerante) → `iptv/model.py` (canal
  normalizado + cabeceras → opciones mpv) → `iptv/sources.py` (fuentes configurables, refresco) → `iptv/index.py` (búsqueda sin acentos)
  → `iptv/radiobrowser.py` → `iptv/store.py` (favoritos/recientes en data_dir) → métodos RPC `iptv.*` → salud opcional (ffprobe, INDEX).
- mu-iptv.lua: menú uosc "TV y radio" (España TV/radio, Mundo por país→categoría, Radio mundial, Favoritos, Recientes, Buscar), botón en
  controles, reproducción con opciones por archivo, zapping ±1 en el grupo, OSD con nombre, ICY en pantalla, grabación (stream-record).
#### H2 · TV y radio — hecho (commits "H2 (parte 1)" y "H2 (parte 2)")
- Backend (parte 1): `mpvd/net.py` caché HTTP (ETag/Last-Modified, TTL 12 h, offline con la última copia), `iptv/m3u.py` parser tolerante
  (tvg-*, group-title, url-tvg, #EXTVLCOPT, #KODIPROP, comillas escapadas, HLS vs lista), `iptv/model.py` canal normalizado + cabeceras →
  opciones de mpv por archivo + sufijos iptv-org `(720p)`/`[Geo-blocked]`/`[Not 24/7]` a campos, `iptv/index.py` búsqueda sin acentos,
  `iptv/store.py` favoritos/recientes/listas de usuario/salud (SQLite), `iptv/sources.py` fuentes (TDTChannels tv+radio, iptv-org) y
  `iptv/service.py` métodos `iptv.*` (sources, refresh, facets, countries, channels, search, channel, play, zap, favorites.*, recents.*,
  sources.add/remove, health.check/status).
- Parte 2 (esta sesión, interactiva): `iptv/radiobrowser.py` (Radio Browser: países, tags, por país, búsqueda, top, contador de clics) con
  métodos `radio.*`; `mpv-config/scripts/mu-iptv/main.lua` + `script-modules/mu/{rpc,uosc}.lua`: menú uosc "TV y radio" (España TV/Radio,
  Mundo país→categoría con nombres y banderas de la API de iptv-org, Radio mundial, Favoritos, Recientes, Mis listas, Buscar en paleta),
  acciones por ítem (favorito, copiar URL, quitar lista), botón `mu-tv` en la barra de uosc, reproducción con opciones por archivo,
  zapping ±1 dentro del grupo, OSD con nombre y título ICY, grabación `stream-record` a `~~desktop/MPV-UOS`, comprobación de salud en
  segundo plano desde la lista. Teclas en input.conf: alt+t menú, alt+f buscar, alt+UP/DOWN zapping, alt+r grabar.
- Correcciones de esta sesión: el estado de navegación sigue a `user-data/uosc/menu/type` leído en nativo (la forma string de una
  sub-clave de user-data es JSON con comillas, por lo que las comparaciones con el tipo nunca acertaban y cada `show()` reabría el menú)
  y ya no depende del evento `close`, que uosc emite desde su hilo en mitad de un reemplazo; los tests aíslan `MPV_UOS_DATA_DIR` por
  ejecución (antes compartían `.cache/data` y un favorito de una pasada se desmarcaba en la siguiente); `wait_property` tolera
  `property not found` en user-data; límite de línea JSON de IPC/JSON-RPC a 32 MiB;
  filas compactas con `geo_blocked`/`not_24_7`; test de red de iptv-org usa el país del probador (MPV_UOS_TEST_COUNTRY, por defecto es)
  y registra como xfail si ningún stream arranca.
- Tests: parser con fixtures reales recortadas (tests/fixtures/iptv), servicio con servidor HTTP local, Radio Browser simulado,
  integración headless mu-iptv (menús, reproducción, zapping, favoritos, grabación real de un directo por HTTP, búsqueda, mundo, radio,
  Mis listas + salud) y @network (descarga de listas reales, directorio Radio Browser, reproduce 3+3+3 canales reales).
- Probar a mano:
  ```bash
  bin/mpv-uos --idle=yes            # alt+t → TV y radio; alt+f busca; Tab sobre un canal → ★ / copiar URL
  .venv/bin/python -m mpvd call iptv.refresh                      # descarga/actualiza listas (caché 12 h)
  .venv/bin/python -m mpvd call iptv.search '{"q":"antena 3","compact":true}'
  .venv/bin/python -m mpvd call iptv.countries '{"source":"iptv_org"}'
  .venv/bin/python -m mpvd call radio.stations '{"country":"es","limit":5,"compact":true}'
  .venv/bin/python -m mpvd call iptv.sources.add '{"name":"Mi lista","url":"https://ejemplo/lista.m3u"}'
  MPV_UOS_TEST_COUNTRY=es uv run pytest -m network tests/test_network_iptv.py -s   # reproduce canales reales
  ```
- Runner nocturno: `tools/nocturno.sh` espera al reset del cupo de sesión de Claude (hora del mensaje o 30 min) sin contarlo como fallo,
  tope de 3 h por iteración, effort `high` (.runner.env) y `DEADLINE=07:30`. Lanzamiento: `tmux kill-session -t mpvuos;
  tmux new-session -d -s mpvuos "cd <proyecto> && systemd-inhibit --what=sleep:idle bash tools/nocturno.sh; exec bash"`.
#### H5 · Subtítulos IA en vivo — plan
- Notion al día (2026-09-29): avances H3/H4, decisiones ADR-019/021 y Próximo paso en la ficha (vía subagente).
- Verificación (subagente → docs/WHISPER.md): whisper.cpp de ~/proyectos/live-captions-linux (copiado a vendor/whisper si sirve,
  si no compilado desde un tag estable), `--help` real de whisper-cli/whisper-server, VAD Silero, formato JSON, RTF por modelo.
- Ya hecho sin depender del modelo: `mpvd/asr/audio.py` (ventana WAV 16 kHz mono con ffmpeg, verificado) y `mpvd/asr/srt.py`
  (segmentos, SRT incremental, fusión por trozos con recorte de solapes) + tests.
- Siguiente: `asr/engine.py` (whisper-server persistente con fallback a whisper-cli), `asr/service.py` (`asr.*`: start/seek/stop/
  status/models; trozos de 10 s con solape, prioridad URGENT para el trozo por delante de time-pos, PRECOMPUTE para el resto y
  el siguiente de la playlist; SRT en caché por hash; eventos push `asr`), `mu-subs.lua` (menú "Subtítulos IA": iniciar/parar,
  idioma, modelo; `sub-add` la primera vez y `sub-reload` en cada actualización; `asr.seek` en seeks; progreso en uosc),
  tools/bench_asr.sh → docs/BENCHMARKS.md, tests con el modelo tiny sobre voz_es/voz_en (palabras clave del manifest).
- Iteración 2 (2026-09-29): binarios de whisper.cpp ya copiados en vendor/whisper (de live-captions-linux, libwhisper 1.9.3,
  funcionan con LD_LIBRARY_PATH); la compilación de vendor/whisper-src (v1.9.4) murió por tiempo → se descarta (ADR-023).
  Diseño: un solo trabajo `heavy` por archivo que recorre un plan de trozos ordenado por distancia a time-pos (seek = mover el
  cursor, sin cancelar trabajos); semáforo global de 1 proceso whisper; SRT estable en .cache/asr/<clave>/ + estado en la caché
  de artefactos (reanudable); modelos bajo demanda desde Hugging Face con verificación de magia ggml; eventos push a mu_subs.
#### H4 · UX base — hecho (commit "H4: UX base")
- mpvd `watch.py` (servicio `watch.*`: get/update/recents/search/remove/clear; clave por contenido; historial de 500 entradas).
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m, botón `mu-menu` en la barra; "Continuar viendo" inline),
  vista Recientes (alt+h; Tab olvida; borrar historial), paleta global (alt+p; comandos de `input-bindings` + curados, canales,
  recientes, acciones de mpvd), continuar viendo (seek al cargar si procede; guarda cada 15 s/pausa/seek/fin; directos excluidos),
  pantalla de inicio en idle. mu-iptv: `mu-iptv-play <id>` y `current_url`. uosc.conf: botones `mu-menu` y `mu-ytdl`.
- docs/ATAJOS.md completo + tests/test_atajos.py (cada tecla de input.conf documentada y cada script-binding existente).
- Tests: test_watch.py (store, clave estable al renombrar, métodos), test_mu_menu.py (reanudación tras renombrar, no reanuda lo
  terminado, menú raíz con recientes, paleta con comandos/canales/recientes/acciones, reproducir canal desde la paleta, pantalla
  de inicio). conftest: `start_screen` en start_mpv (desactivada por defecto).
- Probar a mano:
  ```bash
  bin/mpv-uos                                  # pantalla de inicio (recientes + accesos); alt+p paleta; alt+m menú
  bin/mpv-uos tests/fixtures/media/video30.mkv # avanza a 0:25, cierra con q; cópialo con otro nombre y ábrelo: reanuda en 0:25
  .venv/bin/python -m mpvd call watch.recents
  .venv/bin/python -m mpvd call watch.search '{"q":"video"}'
  ```
#### H4 · UX base — plan
- Verificado: `input-bindings` (mpv 0.41) devuelve `{key, cmd, comment, section, priority, is_weak}`; los comentarios `#!` de
  input.conf llegan como `comment = "! Título > Sub"` → base de la paleta de comandos. `idle-active` + `playlist-count` para la
  pantalla de inicio. `--script-opts=` posterior sustituye la lista entera (usar `--script-opts-append` en tests).
- mpvd `watch.py` (servicio `watch.*`): SQLite en data_dir con clave por contenido (`file.hash` para archivos, `url:` para URLs):
  get/update/recents/search/remove/clear; "terminado" si posición ≥ duración-30 s o ≥95 %.
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m; botón `mu-menu` primero en la barra; incluye "Menú completo"
  = uosc/menu), paleta global (alt+p: comandos de input-bindings + lista curada, canales vía iptv.search, recientes vía
  watch.search, acciones de mpvd), continuar viendo por hash (seek en file-loaded si mpv no reanudó ya; posición cada 15 s,
  en pausa/seek/end-file), pantalla de inicio en idle (opción `mu-menu-start_screen`, desactivada en tests salvo el suyo).
- mu-iptv gana `script-message mu-iptv-play <id>` para la paleta. uosc.conf: `button:mu-menu` y `button:mu-ytdl` en controls.
- docs/ATAJOS.md + test que comprueba que todas las teclas de input.conf están documentadas.
#### H3 · yt-dlp avanzado — hecho (commit "H3: yt-dlp avanzado")
- Verificación real: docs/YTDLP.md (release 2026.08.19, assets y SHA, runtime JS/EJS, `--help` completo, `-J`, progreso JSON,
  fixtures) y docs/MPV_YTDL.md (ytdl_hook embebido en mpv 0.41: opciones, lectura en caliente de script-opts, `user-data/mpv/ytdl/*`,
  sintaxis `loadfile … replace -1 {…}`, pruebas headless).
- Vendorizado: `vendor.lock` fija `yt-dlp` 2026.08.19 (zipimport, SHA-256) y las sumas de deno v2.9.7 por plataforma;
  `tools/vendor.sh` instala vendor/bin/yt-dlp (no pisa una versión más nueva instalada por mpvd) y deno solo si no hay runtime JS.
- mpvd `ytdl/`: `binary.py` (resolución env → vendor → PATH, runtime JS deno/node≥22, ffmpeg, updater diario verificado con
  SHA2-256SUMS), `info.py` (filas de formato con etiqueta/hint, agrupación combinado/vídeo/audio, resumen, playlists planas),
  `presets.py` (DownloadSpec → argv exacto; 14 presets; política de contenedor ADR-019), `downloads.py` (cola sobre JobQueue,
  progreso `MU_PROGRESS`/`MU_PP`/`MU_DONE`, cancelar/repetir/quitar, historial y ajustes persistentes, carpetas XDG),
  `service.py` (métodos `ytdl.status/hook/update.check/update.apply/info/playlist/presets/download/downloads.*/settings.*`,
  caché de `-J` por URL con semilla del hook, eventos push a todas las sesiones). `sessions.push_event` genérico.
- `mu-ytdl/main.lua`: fija `ytdl_hook-ytdl_path` y `ytdl-raw-options js-runtimes` en caliente; conmutador vídeo/solo audio
  (alt+a) en la misma posición; menú Calidad (alt+q) con cambio en caliente y acción "descargar este formato"; menú Descargar
  (alt+d) con presets y opciones conmutables; panel Descargas (alt+l) en vivo desde `mu-event`; Estado de yt-dlp con
  actualización manual; botón ⬇ en la barra con contador; `user-data/mu/ytdl` con estado e items del menú para tests.
- Tests: presets (argv exacto), info (fixtures reales de YouTube/archive.org/playlists), binario+updater (servidor HTTP local,
  SHA incorrecto rechazado), descargas y métodos con un yt-dlp falso (`tests/fixtures/ytdlp/fake_ytdlp.py`), integración headless
  mu-ytdl (ytdl_hook real ejecutando el fake, medios por HTTP, conmutador, calidad, descarga, panel) y @network (info real,
  comprobación en GitHub, descargas reales 360p/mp3 128k/mkv verificadas con ffprobe). conftest aísla watch_later/resume.
- Probar a mano:
  ```bash
  bin/mpv-uos https://www.youtube.com/watch?v=aqz-KE-bpKQ   # alt+q calidad · alt+a solo audio · alt+d descargar · alt+l descargas
  bin/mpv-uos https://archive.org/details/Countdow1960
  .venv/bin/python -m mpvd call ytdl.status
  .venv/bin/python -m mpvd call ytdl.update.check '{"force":true}'
  .venv/bin/python -m mpvd call ytdl.download '{"url":"https://archive.org/details/Countdow1960","preset":"video_360"}'
  .venv/bin/python -m mpvd call ytdl.downloads.list
  uv run pytest -m network tests/test_network_ytdl.py -s
  ```
- Test intermitente conocido: `test_mu_iptv.py::test_menus_play_zap_favorites_record_and_search` puede agotar el tiempo de
  espera del directo (ffmpeg -re) con la máquina cargada; se subió a 90 s. Pasa aislado.
#### H3 · yt-dlp avanzado — plan (original)
- Verificación previa (subagentes): docs/YTDLP.md (última release, assets, runtime JS para YouTube, opciones reales de `--help`,
  formato `-J`, `--progress-template`) y docs/MPV_YTDL.md (ytdl_hook de mpv 0.41: script-opts, `ytdl_path`, `all_formats`,
  `loadfile ... replace -1 {ytdl-format=…,start=…}`, `vid=no` + `audio-display`). Fixtures reales de `-J` en tests/fixtures/ytdlp/.
- Vendorizado: vendor.lock gana `YTDLP_VERSION/URL/SHA256` (asset `yt-dlp` zipimport, corre con el Python del .venv); tools/vendor.sh lo
  instala en vendor/bin/yt-dlp (+ envoltorio ejecutable). mpvd comprueba una vez al día (`ytdl.update.check`) la última release en
  GitHub (caché HTTP) y la descarga verificando el SHA2-256SUMS oficial; nunca en el hilo de mpv. Runtime JS (deno) vendorizado en
  vendor/bin si YouTube lo exige (verificar en docs/YTDLP.md).
- ytdl_hook: `script-opts/ytdl_hook.conf` con `ytdl_path=<ruta absoluta>` no vale (la config debe ser portable) → mu-ytdl fija
  `ytdl_hook-ytdl_path` en caliente vía `script-opts` al arrancar apuntando a vendor/bin (relativo a MPV_UOS_ROOT).
- mpvd `ytdl/`: `binary.py` (localizar/actualizar binario), `info.py` (`-J` con caché por URL, agrupación de formatos), `presets.py`
  (argumentos de cada preset: exacto/combinación/audio original/convertido/opciones extra), `downloads.py` (cola con progreso
  `--newline --progress-template`, cancelar, reintentar, carpetas XDG y plantilla), `service.py` (métodos `ytdl.*`).
- mu-ytdl.lua: conmutador vídeo/solo audio (tecla + menú, `loadfile … replace -1 {ytdl-format=…,start=<pos>}` conservando pausa,
  velocidad y volumen), menú "Calidad" (todos los formatos agrupados) y "Descargar" (presets), panel "Descargas" con progreso
  (eventos push `mu-event` desde mpvd), botón en la barra de uosc y teclas en input.conf.
- Tests: unit (presets → argv exacto, parseo de `-J` desde fixtures, parser de progreso, comprobación de actualización con servidor
  local), integración headless (mu-ytdl con un yt-dlp falso que devuelve el JSON de fixture y "descarga" un archivo local con
  progreso), @network (descarga real corta en 2 presets verificada con ffprobe).

### Sesión interactiva · 2026-09-30 · H14 (pruebas de Ser)
Ser probó la app y reportó 9 problemas; se diagnosticaron en 6 frentes (tmp/diag-*) y se arreglaron (núcleo en la sesión; yt-dlp,
intro, subtítulos, preferencias y TV en agentes con worktree, fusionados). `tools/check.sh`: lint 0 avisos, 292 tests sin red y 8 con
red en verde (un test real de YouTube falló una vez por la red y pasó al repetir).
- **Núcleo**: el reproductor nunca se cierra al fallar una carga y explica la causa en español; vuelve a la pantalla de inicio;
  socket por instancia y sesiones de mpvd por PID; datos de usuario en `~/.local/share/mpv-uos` (migrados); watch_later solo por
  archivo (arregla audio perdido al cambiar de archivo y La 1 fijada a 360p); hwdec=vaapi primero; desentrelazado con copia.
- **Interfaz**: botón play/pausa; teclas de mpv restauradas (`v`, `ctrl+alt+v`) y agrupadas en «Más opciones»; `ctrl+v` abre el
  portapapeles; paleta más certera; «Abrir URL» (`ctrl+u`) y «Buscar en YouTube» (`ctrl+f`).
- **yt-dlp**: runtime JS desde el primer vídeo, H.264 primero, preset mp4 real, búsqueda en YouTube; recuerda «solo audio».
- **Subtítulos**: `alt+S` guarda en SRT (IA, traducción, resync, pista interna); motor de traducción «Calidad» (OPUS-MT, ya en
  vendor/models/opus-mt para es/ca↔en); pre-subtitulado con small-q8_0 y trozos de 28,5 s con contexto.
- **Saltar intro**: episodios en carpetas hermanas, avisos visibles, versiones del mismo vídeo descartadas, marcado manual,
  salto automático con cuenta atrás, temporada completa; nada se escribe junto a los vídeos salvo «Exportar».
- **Preferencias**: se recuerdan volumen, velocidad, subtítulos, imagen, idiomas, filtros, opciones de cada módulo…;
  Preferencias › Restablecer.
- **TV**: directos sin watch_later, HLS estable, User-Agent de navegador, calidad visible, copias con anuncios detrás del oficial y
  cambio automático de fuente, países y categorías en español. Lo que queda (720p de RTVE, 25 fps, bitrate bajo) es de la fuente.
- **Mando**: el QR avisa del cortafuegos con la orden exacta. **Instalador**: carpeta movida, MIME completos, restaura reproductores.
- Probar a mano:
  ```bash
  mpv-uos                                   # inicio · ctrl+u abrir URL · ctrl+f buscar en YouTube · alt+t TV
  mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+a solo audio (se recuerda) · alt+q calidad · alt+d descargar
  mpv-uos ~/Descargas/…/Don\ Matteo\ 1x06…mp4   # «Analizando…», luego alt+k salta intro · alt+j menú
  mpv-uos pelicula.mkv                      # alt+i subtítulos IA · Traducir (Calidad) · alt+S guardar SRT
  ```
