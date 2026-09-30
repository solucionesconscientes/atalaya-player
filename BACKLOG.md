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
- [x] Saltar intro: episodios en carpetas hermanas, avisos visibles, comprobaciones de sentido, bordes baratos, marcado manual,
      salto automático con cuenta atrás, temporada completa.
- [x] Subtítulos: guardar en SRT (IA, traducción, resync, pista embebida), traducción OPUS-MT big, transcripción small-q8_0 con
      trozos de 28,5 s y contexto.
- [x] Preferencias persistentes (mu-prefs + módulo mu/prefs) y "Restablecer preferencias".
- [x] TV: calidad de canales (según diagnóstico), nombres de países y categorías en español, duplicados.

## Plan aprobado por Ser el 2026-09-30 (H15–H28)
Decisiones de Ser: ok.ru queda solo como reproducir/descargar por enlace (sin búsqueda); el mando QR se queda como está (solo LAN);
el túnel de internet se acepta SOLO mientras haya una sala de "Compartir" abierta. Todo lo que dependa de la nube o de cuentas,
desactivado por defecto. Un hito marcado «DECISIÓN PENDIENTE» no se implementa hasta que Ser decida (sáltalo).

## H15 · Interfaz y navegación más simples
- [x] Un único menú con migas en el título («MPV-UOS › TV y radio › España»), «‹ Atrás» como primera fila de cada submenú (ratón),
      Retroceso/← vuelve un nivel y Esc cierra; «atrás» en la raíz de un módulo vuelve al menú principal (pila de navegación
      compartida entre scripts: módulo `mu/nav`). Menú principal en 7–8 categorías con icono: Abrir, TV y radio, Descargas y
      conversión, Subtítulos, Imagen y sonido, Grabar, Herramientas, Preferencias.
- [x] Barra de uosc reducida (reproducción, subtítulos, audio, velocidad, menú, grabar, pantalla completa); el resto en el menú.
      Letra de menú algo mayor, textos cortos sin tecnicismos, `?` muestra una ayuda en pantalla, clic en el vídeo para pausar como
      preferencia (desactivada por defecto). Tests headless de navegación (entrar, atrás, cerrar) en todos los módulos.

## H16 · Sincronía de los subtítulos IA
- [ ] Tiempos por palabra de whisper.cpp (verifica las opciones reales de la versión vendorizada: --dtw / -ml / -sow / tokens),
      cortes de líneas largas en tiempos reales de palabra, inicio y fin ajustados a los tramos de voz del VAD, reglas de lectura
      (mín./máx. duración, caracteres por segundo, sin solaparse). Test con audio de tiempos conocidos (desfase medio < 150 ms).

## H17 · «Mis notas»
- [ ] Menú Mis notas (por vídeo, saltar al minuto, editar, borrar), ficheros con el título legible en `<datos>/notas`, exportar junto
      al vídeo o a una carpeta elegida (Obsidian), y enlaces `mpv-uos://` registrados en la entrada de escritorio que abren el vídeo en
      ese minuto (x-scheme-handler; sin sudo).

## H18 · Botón «Grabar» unificado
- [ ] Botón ● en la barra con menú: captura (con/sin subtítulos), grabar vídeo desde ahora, grabar solo audio, recortar tramo; punto
      rojo y contador mientras graba. Directos/TV/radio: stream-record; vídeos de internet: yt-dlp --download-sections del tramo
      (verifica la opción); archivos locales: corte sin recodificar. Carpeta configurable. Tests.

## H19 · Gestor de descargas avanzado
- [ ] Varias URLs a la vez (pegar lista / fichero), listas y canales con casillas (flat playlist), carpeta y numeración por lista,
      archivo de descargas (sin duplicados), simultáneas y límite de velocidad configurables, la cola sobrevive a reinicios.
- [ ] Subtítulos en SRT: «solo subtítulos» o junto al vídeo, eligiendo idiomas (por defecto originales + es + en; «todos» explícito).
- [ ] yt-dlp: reintento automático con la versión nightly cuando la estable falle (ok.ru hoy), `curl_cffi` en el .venv para la
      suplantación de navegador (TikTok), opción desactivada «usar mi sesión del navegador» (cookies, nunca DRM). TikTok por enlace y
      perfil completo con casillas; Instagram por enlace.

## H20 · Convertir vídeo y audio
- [ ] «Convertir» en el menú: MP4 compatible (H.264/AAC), más pequeño (H.265), web, solo audio (MP3/M4A/Opus/FLAC/WAV), GIF; límite
      de resolución y calidad, tramo, conservar subtítulos, carpeta entera; codificación por hardware VA-API si `vainfo` la ofrece;
      cola unificada con descargas (panel «Tareas»). Tests.

## H21 · Guía de TV y grabación programada
- [ ] EPG de TDTChannels (url-tvg de la lista, XMLTV .gz) en caché: «ahora / después» en cada canal y parrilla por canal.
- [ ] Grabación programada desde la guía o a mano (canal, inicio, fin), aunque se esté viendo otra cosa (ffmpeg en mpvd), con aviso
      al terminar; lista de grabaciones programadas y realizadas.

## H22 · Biblioteca y subtítulos automáticos
- [ ] Biblioteca sin servidor: carpetas elegidas, películas y series por temporada, carátulas locales (y metadatos opcionales con clave
      propia, desactivado), «seguir viendo» y «siguiente episodio» automático en la pantalla de inicio.
- [ ] Subtítulos de internet por hash (OpenSubtitles, cuenta propia, desactivado por defecto) con resincronización automática.

## H23 · Suscripciones, panel web y automatismos
- [ ] Suscripciones a canales, listas y podcasts (RSS) con reglas (calidad, solo audio, conservar N, borrar lo visto), horarios de
      descarga, límite por franja y pausa con red medida.
- [ ] Cadena tras descargar (SponsorBlock, volumen igualado, subtítulos IA + traducción, renombrar y mover a la biblioteca).
- [ ] Panel web de descargas servido por mpvd (misma base que la PWA del mando): tabla, selección múltiple, arrastrar enlaces,
      historial, espacio en disco; «Enviar a MPV-UOS» desde el navegador (marcador + `mpv-uos://`); aviso al móvil al terminar.

## H24 · Escritorio y sonido
- [ ] MPRIS (controles de KDE, teclas multimedia, auriculares, pantalla de bloqueo), volumen igualado entre vídeos, ecualizador sencillo.

## H25 · Compartir: salas, ver juntos y emitir
- [ ] Sala privada con enlace: «ver juntos» sincronizado (cada invitado reproduce la fuente en su navegador), permisos por invitado
      (solo ver / puede controlar, con aprobación en pantalla, revocable), quién está conectado, avisos «Ana ha pausado».
- [ ] Retransmisión de archivos locales a los invitados (HLS con subtítulos WebVTT, conversión al vuelo por VA-API si hace falta).
- [ ] Túnel de Cloudflare (cloudflared en vendor/, sin cuenta) activo SOLO mientras la sala está abierta, con caducidad y límite de
      intentos. Sala pública «solo ver» (cualquiera con el enlace, número máximo de espectadores, sin control ni chat).
- [ ] «Emitir en directo» a una plataforma (YouTube Live, Twitch, PeerTube, Owncast) por RTMP con clave de emisión, para audiencias
      grandes. Chat y reacciones en salas privadas. Aviso legal: solo contenido que se puede compartir.

## Ampliación aprobada por Ser el 2026-09-30 (H29–H33, se hacen antes de H26–H28)
Decisión de Ser: NADA que traiga o pueda traer retraso. Fuera: subtítulos IA en directos, TV o radio, «directo en diferido» y
traducción en directo. Fuera también: imagen (mejoras de imagen, visor de fotos), registro de escuchas (scrobbling) y torrents.

## H29 · Subtítulos de vídeos de internet (sin retraso)
- [ ] Subtítulos que da la web (manuales y automáticos de YouTube; los de otras webs que yt-dlp exponga) activables desde el panel
      de subtítulos con elección de idioma (write-auto-subs / sub-langs verificados en `yt-dlp --help`); traducción offline del archivo
      completo antes de mostrarla (nunca frase a frase en directo); «Guardar SRT» también para estas pistas. Test @network con un
      vídeo de YouTube de más de 1 min con subtítulos automáticos.

## H30 · TV: subtítulos y audios del canal, búsqueda por categoría
- [ ] Pistas propias del canal con nombres legibles (RTVE trae WebVTT es/en/gl/ca/eu; audio «qaa» = Versión original, «ads» =
      Audiodescripción), distintivos CC / VO / AD en la lista de canales y acceso rápido desde el menú. Sin traducción en directo.
- [ ] Buscador dentro de cada categoría general: en España TV, España radio, cada país de Mundo, Radio mundial, Favoritos y
      Recientes, «Buscar en esta lista» filtra solo sus canales (sin acentos, mismo motor que la búsqueda global).

## H31 · Formatos de descarga y decodificación del equipo
- [ ] Detectar qué códecs decodifica la gráfica (vainfo en Linux; D3D11/DXVA en Windows y VideoToolbox en macOS documentados) y
      etiquetar en Calidad y Descargar «fluido en tu equipo» / «exigente (por procesador)».
- [ ] Valores por defecto: vídeo original sin recodificar hasta 1080p con sus fps, códec preferido el que el equipo decodifica por
      hardware, audio Opus original, contenedor MP4 (MKV como opción); solo audio en Opus original (MP3 320 kb/s y M4A como opciones);
      HEVC solo como perfil «Más pequeño» en Convertir. Tamaños orientativos por hora en la ayuda.

## H32 · Audio de primer nivel
- [ ] Biblioteca musical (artista, álbum, año, género, carátulas, búsqueda); listas (crear, ordenar, guardar M3U8, cola «reproducir a
      continuación», listas inteligentes, historial); sin cortes entre pistas y fundido opcional; volumen igualado por pista y álbum
      (ReplayGain calculado en segundo plano si falta); ecualizador con perfiles de auriculares; salida exclusiva opcional.
- [ ] Letras sincronizadas y carátulas; audiolibros y podcasts (marcadores, posición y velocidad por libro, capítulos, temporizador de
      apagado); identificar y etiquetar canciones (opcional, desactivado). Sin registro de escuchas.
- [ ] «Solo audio» para cualquier fuente (instantáneo en archivos locales; en internet recarga solo el audio) y opción de pasar a
      solo audio al minimizar la ventana. Medido: ~3× menos CPU que decodificar por gráfica y ~10× menos que por procesador.

## H33 · Nombre e identidad: Sintonía
- [ ] Nombre público «Sintonía» (comando `sintonia`, entrada de escritorio, título de ventana, pantalla de inicio, PWA, textos y docs;
      el repositorio y el slug interno siguen siendo mpv-uos). Logo aprobado «C · Anillo» (anillo de progreso azul señal #3D7BFF sobre
      tinta #0D1320, punto ámbar #FFB020 de «en antena», play blanco; marca «sintonía» en minúsculas con el punto ámbar) como icono de
      la app, de la PWA y de la bandeja; versión monocroma y variante sin fondo. El ámbar es el color de estado «en directo /
      grabando / descargando» en toda la interfaz.

## H26 · Torrents · FUERA POR AHORA (decisión de Ser, 2026-09-30: no implementar)
- [~] Propuesta aparcada: integrarse con qBittorrent (ya instalado; Ser activa su interfaz web en localhost) para añadir magnets, descargar en
      orden y «ver mientras descarga» en MPV-UOS, con libtorrent en el .venv solo como alternativa si no hay qBittorrent.

## H27 · Diferenciales
- [ ] Enviar a la tele (Chromecast/DLNA), mini reproductor flotante, modo salón (letra grande, mando HDMI-CEC o gamepad), modo sencillo,
      «¿qué me he perdido?» con modelo local (si el rendimiento lo permite).

## H28 · Plataformas
- [ ] Paquete Linux (AppImage y/o Flatpak), prueba en ARM64 (Raspberry Pi 5 / modo salón), Windows (named pipes en mpvd, lanzador,
      yt-dlp.exe, whisper, instalador) y macOS (.app). Lo que no se pueda probar sin el hardware, a docs/PLATAFORMAS.md y NEEDS_HUMAN.md.
