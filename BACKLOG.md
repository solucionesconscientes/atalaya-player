# BACKLOG — orden estricto. [ ] pendiente · [x] hecho (tests en verde) · [~] bloqueado (ver NEEDS_HUMAN.md)
Si terminas todo, añade hitos nuevos al final a partir de docs/VISION.md (TOP 10 primero) y continúa.

## H0 · Cimientos
- [x] Estructura, .gitignore, README mínimo, pyproject con uv (paquete mpvd), pytest configurado.
- [x] bin/mpv-uos: lanza el mpv del sistema con --config-dir=<proyecto>/mpv-config y un --input-ipc-server único por instancia.
- [x] mpv-config: mpv.conf (vo=gpu-next, hwdec=auto-safe, save-position-on-quit, osc=no, osd-bar=no), input.conf base,
      uosc (última estable) y thumbfast vendorizados con versión fijada en vendor.lock.
- [x] tools/make_test_media.sh: vídeo de 30 s (testsrc2 + tono), audio con voz sintetizada (espeak-ng) en español e inglés con frases
      conocidas, un archivo con capítulos.
- [x] tools/check.sh: pytest + lint Lua + smoke test headless (mpv carga uosc y mu-core sin errores en el log).
- [x] Registro en Notion (opcional, ver CLAUDE.md).
Aceptación: tools/check.sh en verde; README explica cómo abrir un archivo con bin/mpv-uos.

## H1 · mpvd núcleo (A1, A2, A7)
- [x] Servidor JSON-RPC 2.0 (socket Unix en ruta de runtime): ping, version, capabilities, jobs.list, jobs.cancel; registro de sesiones
      (una por instancia de mpv, con su ruta IPC).
- [x] mu-core.lua: arranca mpvd si no responde (proceso desacoplado con el Python del .venv), se registra, publica estado en user-data/mu/*,
      reintenta y registra en log.
- [x] Hash de archivo (tamaño + 64 KB inicio/fin, estilo OpenSubtitles) y caché SQLite de artefactos (get/put/invalidate).
- [x] Cola asyncio con prioridades y cancelación; guardián de rendimiento básico (observa frame-drop-count y frena trabajos pesados).
Aceptación: tests unitarios + integración mpv headless ↔ mpvd.

## H2 · TV y radio (N1)
- [x] Fuentes configurables: TDTChannels TV (+ radio/tvradio si existen), iptv-org index.m3u (+ índices por país/categoría si existen),
      Radio Browser para radio mundial, y M3U/URLs propias del usuario.
- [x] Descarga con caché (ETag/Last-Modified, TTL 12 h, modo offline con la última copia) y parser M3U/M3U8 robusto
      (#EXTINF con tvg-*, group-title, url-tvg; #EXTVLCOPT; #KODIPROP; líneas rotas; duplicados).
- [x] Modelo normalizado de canal {id, nombre, urls, tipo tv/radio, país, idioma, grupo, logo, cabeceras} e índice de búsqueda sin acentos.
- [x] uosc: menú "TV y radio" → España TV / España radio / Mundo (país → categoría) / Radio mundial / Favoritos / Recientes / Buscar;
      búsqueda tipo paleta; acciones por ítem (favorito, copiar URL); botón en la barra de controles.
- [x] Reproducción con cabeceras correctas por archivo; zapping ±1 dentro del grupo con teclas documentadas; OSD con el nombre del canal;
      radio: título ICY en pantalla y grabación del directo (stream-record) a carpeta configurable.
- [x] Comprobación de salud opcional en segundo plano (ffprobe con timeout, baja prioridad) que marca canales caídos.
Aceptación: tests del parser con fixtures reales recortadas de las fuentes; test @network que descarga las listas y reproduce en headless
≥3 canales de TDTChannels y ≥3 de iptv-org (se admiten fallos por geobloqueo; quedan registrados).

## H3 · yt-dlp avanzado (N2)
- [x] yt-dlp oficial más reciente vendorizado en vendor/bin, con comprobación de actualización como máximo diaria; dependencias actuales
      que exija (verifícalo; p. ej. runtime JS para YouTube) también vendorizadas; ytdl_hook configurado para usarlo.
- [x] Conmutador vídeo / solo audio (uosc + tecla) que recarga la URL en la posición actual (solo audio: ytdl-format=bestaudio, vid=no,
      portada si existe).
- [x] Menú "Calidad": mpvd obtiene `yt-dlp -J` (caché por URL) y lista TODOS los formatos agrupados (vídeo+audio, solo vídeo, solo audio)
      con contenedor, códecs, resolución, fps, HDR, bitrate y tamaño; elegir cambia el formato en caliente.
- [x] Menú "Descargar": formato exacto o combinación elegida con contenedor mp4/mkv/webm (remux si hace falta); audio original sin
      recodificar; audio convertido a mp3/opus/m4a/flac/wav con 96/128/160/192/256/320 kbps o VBR; opciones: subtítulos, capítulos,
      miniatura, metadatos, SponsorBlock (marcar/quitar), playlist completa o solo el elemento.
- [x] Cola de descargas en mpvd: progreso (%, velocidad, ETA) visible en uosc, cancelar, reintentar, notificación al terminar;
      carpetas XDG por defecto (Vídeos/Música)/MPV-UOS y plantilla de nombre configurables.
Aceptación: tests con JSON `-J` guardado como fixture (parseo + argumentos de cada preset); test @network que descarga un vídeo corto
de licencia libre en 2 presets (vídeo 360p y mp3 128k) y verifica con ffprobe códec y bitrate.

## H4 · UX base (G1, F2, G7)
- [x] Menú raíz "MPV-UOS" y botón en los controles; paleta de comandos global (comandos + canales + recientes + acciones de mpvd).
- [x] Continuar viendo por hash (independiente de la ruta) y pantalla de inicio en modo idle con recientes y accesos.
- [x] docs/ATAJOS.md con todas las teclas.

## H5 · Subtítulos IA en vivo (B1, B2)
- [x] whisper.cpp: reutiliza el de ~/proyectos/live-captions-linux si existe (copiar/compilar en vendor/, sin tocar el original); si no, clona
      un tag estable y compila en vendor/ (CPU; Vulkan solo si compila y mejora el benchmark). Preferir whisper-server (modelo cargado una vez).
- [x] Modelo elegido según hardware con tools/bench_asr.sh (RTF guardado en docs/BENCHMARKS.md); modelos + VAD Silero descargados bajo demanda.
- [x] Look-ahead: mpvd extrae audio por delante de time-pos (ffmpeg, 16 kHz mono), VAD, trocea, transcribe y escribe un SRT incremental en
      caché por hash; mu-subs.lua lo añade como pista y lo recarga; en un seek se repriorizan los trabajos.
- [x] Pre-subtitulado del siguiente elemento de la playlist en baja prioridad; progreso visible en uosc; idioma automático o fijado desde menú.
Aceptación: con el audio de prueba ES/EN el SRT contiene las frases esperadas (por palabras clave); latencia documentada; sin drops relevantes.

## H6 · Sincronía, traducción y duales (B5, B4, C1-básico)
- [x] Resincronizar un SRT externo contra las palabras de Whisper (DTW, deriva por tramos) desde el menú de subtítulos.
- [x] Traducción offline rápida (Argos Translate u opus-mt con CTranslate2) con caché por hash + par de idiomas; ruta opcional con LLM local si existe.
- [x] Subtítulos duales (secondary-sid) con original arriba y traducción abajo, activables desde menú.

## H7 · Sonido e imagen (B13, B14, B15, D5, D8, D9)
- [x] Menú "Sonido e imagen": diálogo claro (niveles lavfi), modo noche, reducción de ruido (arnndn con modelo descargado), binaural (sofalizer
      si hay SOFA libre), protección fotosensible (filtro photosensitivity) y diagnóstico de tirones. Cada filtro validado contra el mpv instalado.

## H8 · MCP rico (H1, H2)
- [x] Servidor MCP (stdio) en mpvd: tools status/play/pause/seek/search_dialogue/list_channels/play_channel/download/add_note y resource de
      transcripción; acciones destructivas con confirmación en OSD; .mcp.json.example + instrucciones en README; tests con cliente MCP.

## H9 · Salto de intro/créditos local (B10)
- [x] Chromaprint (fpcalc) entre episodios de la carpeta + blackdetect/silencedetect → segmentos; botón "Saltar"; exportar media segments JSON.

## H10 · Búsqueda semántica y capítulos automáticos (B7, B9)
- [x] Embeddings multilingües ligeros (ONNX) + sqlite-vec sobre la transcripción; integrado en la paleta; capítulos por cambio de tema.

## H11 · Estudio (C4, C6, C8, C10)
- [x] Repetir línea, velocidad inteligente (silencios), notas → Markdown con enlaces de tiempo, clips/GIF desde el bucle A-B.

## H12 · Mando QR/PWA (E3)
- [x] PWA local servida por mpvd con token de un solo uso mostrado como QR en OSD; control, canales y búsqueda.

## H13 · Cierre
- [x] README completo, docs/USO.md, instalación de usuario en Linux, notas Windows/macOS en docs/PLATAFORMAS.md, resumen final en PROGRESS.md.

## H14 · Pruebas de Ser (2026-09-30): arreglos y pulido
Informe de Ser tras probar: falta play/pausa, yt-dlp "no funciona", traducción floja, guardar SRT, canales que se ven mal, saltar intro
no funciona, recordar opciones, reproducir YouTube y otras webs, el QR del móvil no funciona. Diagnóstico en 6 frentes (tmp/diag-*).
- [x] Núcleo: el reproductor no se cierra al fallar una carga (idle siempre), errores de carga explicados en pantalla, vuelta a la
      pantalla de inicio, socket por instancia (nunca heredado) y sesiones de mpvd por PID, futuro huérfano en el cliente IPC,
      datos de usuario en XDG con migración, watch_later sin opciones globales (sin audio al cambiar de archivo), mpv.conf
      (vaapi primero, deinterlace=auto, H.264 primero en yt-dlp, app-id de Wayland), barra con play/pausa, teclas de mpv restauradas
      (v, ctrl+alt+v) y agrupadas en el menú, portapapeles nativo, paleta (coincidencia por palabra, sinónimos, canales primero,
      resultados locales al instante), mando: aviso del cortafuegos con la orden exacta, instalador (carpeta movida, MIME del mpv
      del sistema, restaurar reproductores por defecto).
- [x] yt-dlp: runtime JS desde el primer vídeo, preset mp4 en H.264/AAC, "Abrir URL…" y "Buscar en YouTube".
- [ ] Saltar intro: episodios en carpetas hermanas, avisos visibles, comprobaciones de sentido, bordes baratos, marcado manual,
      salto automático con cuenta atrás, temporada completa.
- [ ] Subtítulos: guardar en SRT (IA, traducción, resync, pista embebida), traducción OPUS-MT big, transcripción small-q8_0 con
      trozos de 28,5 s y contexto.
- [ ] Preferencias persistentes (mu-prefs + módulo mu/prefs) y "Restablecer preferencias".
- [ ] TV: calidad de canales (según diagnóstico), nombres de países y categorías en español, duplicados.
