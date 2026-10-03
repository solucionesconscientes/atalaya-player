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
- [x] Tiempos por palabra de whisper.cpp (verifica las opciones reales de la versión vendorizada: --dtw / -ml / -sow / tokens),
      cortes de líneas largas en tiempos reales de palabra, inicio y fin ajustados a los tramos de voz del VAD, reglas de lectura
      (mín./máx. duración, caracteres por segundo, sin solaparse). Test con audio de tiempos conocidos (desfase medio < 150 ms).

## H17 · «Mis notas»
- [x] Menú Mis notas (por vídeo, saltar al minuto, editar, borrar), ficheros con el título legible en `<datos>/notas`, exportar junto
      al vídeo o a una carpeta elegida (Obsidian), y enlaces `mpv-uos://` registrados en la entrada de escritorio que abren el vídeo en
      ese minuto (x-scheme-handler; sin sudo).

## H18 · Botón «Grabar» unificado
- [x] Botón ● en la barra con menú: captura (con/sin subtítulos), grabar vídeo desde ahora, grabar solo audio, recortar tramo; punto
      rojo y contador mientras graba. Directos/TV/radio: stream-record; vídeos de internet: yt-dlp --download-sections del tramo
      (verifica la opción); archivos locales: corte sin recodificar. Carpeta configurable. Tests.

## H19 · Gestor de descargas avanzado
- [x] Varias URLs a la vez (pegar lista / fichero), listas y canales con casillas (flat playlist), carpeta y numeración por lista,
      archivo de descargas (sin duplicados), simultáneas y límite de velocidad configurables, la cola sobrevive a reinicios.
- [x] Subtítulos en SRT: «solo subtítulos» o junto al vídeo, eligiendo idiomas (por defecto originales + es + en; «todos» explícito).
- [x] yt-dlp: reintento automático con la versión nightly cuando la estable falle (ok.ru hoy), `curl_cffi` en el .venv para la
      suplantación de navegador (TikTok), opción desactivada «usar mi sesión del navegador» (cookies, nunca DRM). TikTok por enlace y
      perfil completo con casillas; Instagram por enlace.

## H20 · Convertir vídeo y audio
- [x] «Convertir» en el menú: MP4 compatible (H.264/AAC), más pequeño (H.265), web, solo audio (MP3/M4A/Opus/FLAC/WAV), GIF; límite
      de resolución y calidad, tramo, conservar subtítulos, carpeta entera; codificación por hardware VA-API si `vainfo` la ofrece;
      cola unificada con descargas (panel «Tareas»). Tests.

## H21 · Guía de TV y grabación programada
- [x] EPG de TDTChannels (url-tvg de la lista, XMLTV .gz) en caché: «ahora / después» en cada canal y parrilla por canal.
- [x] Grabación programada desde la guía o a mano (canal, inicio, fin), aunque se esté viendo otra cosa (ffmpeg en mpvd), con aviso
      al terminar; lista de grabaciones programadas y realizadas.

## H22 · Biblioteca y subtítulos automáticos
- [x] Biblioteca sin servidor: carpetas elegidas, películas y series por temporada, carátulas locales (y metadatos opcionales con clave
      propia, desactivado), «seguir viendo» y «siguiente episodio» automático en la pantalla de inicio.
- [x] Subtítulos de internet por hash (OpenSubtitles, cuenta propia, desactivado por defecto) con resincronización automática.

## H23 · Suscripciones, panel web y automatismos
- [x] Suscripciones a canales, listas y podcasts (RSS) con reglas (calidad, solo audio, conservar N, borrar lo visto), horarios de
      descarga, límite por franja y pausa con red medida.
- [x] Cadena tras descargar (SponsorBlock, volumen igualado, subtítulos IA + traducción, renombrar y mover a la biblioteca).
- [x] Panel web de descargas servido por mpvd (misma base que la PWA del mando): tabla, selección múltiple, arrastrar enlaces,
      historial, espacio en disco; «Enviar a MPV-UOS» desde el navegador (marcador + `mpv-uos://`); aviso al móvil al terminar.

## H24 · Escritorio y sonido
- [x] MPRIS (controles de KDE, teclas multimedia, auriculares, pantalla de bloqueo), volumen igualado entre vídeos, ecualizador sencillo.

## H25 · Compartir: salas, ver juntos y emitir
- [x] Sala privada con enlace: «ver juntos» sincronizado (cada invitado reproduce la fuente en su navegador), permisos por invitado
      (solo ver / puede controlar, con aprobación en pantalla, revocable), quién está conectado, avisos «Ana ha pausado».
- [x] Retransmisión de archivos locales a los invitados (HLS con subtítulos WebVTT, conversión al vuelo por VA-API si hace falta).
- [x] Túnel de Cloudflare (cloudflared en vendor/, sin cuenta) activo SOLO mientras la sala está abierta (ADR-068):
      `mpvd/share/tunnel.py`, opción *Compartir → Que se pueda entrar desde internet* (apagada por defecto, se recuerda),
      binario opcional (`MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh`, fijado por SHA-256). Probado con un cloudflared falso
      (tests/test_share_tunnel.py) y con un túnel real: la dirección pública sirve lo que sirve el servidor local y el
      proceso muere con la sala. Con túnel no hace falta tocar el cortafuegos.
- [x] Sala pública «solo ver» (cualquiera con el enlace en la red local, número máximo de espectadores, sin control ni chat).
- [x] «Emitir en directo» a una plataforma (YouTube Live, Twitch, PeerTube, Owncast) por RTMP con clave de emisión, para audiencias
      grandes. Chat y reacciones en salas privadas. Aviso legal: solo contenido que se puede compartir.

## Ampliación aprobada por Ser el 2026-09-30 (H29–H33, se hacen antes de H26–H28)
Decisión de Ser: NADA que traiga o pueda traer retraso. Fuera: subtítulos IA en directos, TV o radio, «directo en diferido» y
traducción en directo. Fuera también: imagen (mejoras de imagen, visor de fotos), registro de escuchas (scrobbling) y torrents.

## H29 · Subtítulos de vídeos de internet (sin retraso)
- [x] Subtítulos que da la web (manuales y automáticos de YouTube; los de otras webs que yt-dlp exponga) activables desde el panel
      de subtítulos con elección de idioma (write-auto-subs / sub-langs verificados en `yt-dlp --help`); traducción offline del archivo
      completo antes de mostrarla (nunca frase a frase en directo); «Guardar SRT» también para estas pistas. Test @network con un
      vídeo de YouTube de más de 1 min con subtítulos automáticos.

## H30 · TV: subtítulos y audios del canal, búsqueda por categoría
- [x] Pistas propias del canal con nombres legibles (RTVE trae WebVTT es/en/gl/ca/eu; audio «qaa» = Versión original, «ads» =
      Audiodescripción), distintivos CC / VO / AD en la lista de canales y acceso rápido desde el menú. Sin traducción en directo.
- [x] Buscador dentro de cada categoría general: en España TV, España radio, cada país de Mundo, Radio mundial, Favoritos y
      Recientes, «Buscar en esta lista» filtra solo sus canales (sin acentos, mismo motor que la búsqueda global).

## H31 · Formatos de descarga y decodificación del equipo
- [x] Detectar qué códecs decodifica la gráfica (vainfo en Linux; D3D11/DXVA en Windows y VideoToolbox en macOS documentados) y
      etiquetar en Calidad y Descargar «fluido en tu equipo» / «exigente (por procesador)».
- [x] Valores por defecto: vídeo original sin recodificar hasta 1080p con sus fps, códec preferido el que el equipo decodifica por
      hardware, audio Opus original, contenedor MP4 (MKV como opción); solo audio en Opus original (MP3 320 kb/s y M4A como opciones);
      HEVC solo como perfil «Más pequeño» en Convertir. Tamaños orientativos por hora en la ayuda.

## H32 · Audio de primer nivel
- [x] Biblioteca musical (artista, álbum, año, género, carátulas, búsqueda); listas (crear, ordenar, guardar M3U8, cola «reproducir a
      continuación», listas inteligentes, historial); sin cortes entre pistas y fundido opcional; volumen igualado por pista y álbum
      (ReplayGain calculado en segundo plano si falta); ecualizador con perfiles de auriculares; salida exclusiva opcional.
- [x] Letras sincronizadas y carátulas; audiolibros y podcasts (marcadores, posición y velocidad por libro, capítulos, temporizador de
      apagado); identificar y etiquetar canciones (opcional, desactivado). Sin registro de escuchas.
- [x] «Solo audio» para cualquier fuente (instantáneo en archivos locales; en internet recarga solo el audio) y opción de pasar a
      solo audio al minimizar la ventana. Medido: ~3× menos CPU que decodificar por gráfica y ~10× menos que por procesador.

## H33 · Identidad: logo (el nombre lo decide Ser más adelante)
- [x] Logo aprobado «C · Anillo» (docs/marca/logo-anillo.svg: anillo de progreso azul señal #3D7BFF sobre tinta #0D1320, punto
      ámbar #FFB020 de «en antena», play blanco) como icono de la app, de la entrada de escritorio, de la PWA y de la bandeja; versión
      monocroma y variante sin fondo. El ámbar es el color de estado «en directo / grabando / descargando» en toda la interfaz.
- NOMBRE PENDIENTE: no renombrar nada. La app sigue llamándose MPV-UOS hasta que Ser decida (candidato descartado de momento:
  «Sintonía»). Deja el nombre centralizado en un solo sitio (constante/Config) para que el cambio posterior sea trivial.

## H26 · Torrents · REABIERTO como H59 (Ser, 2026-10-03)
- [~] La propuesta de 2026-09-30 (integrarse con qBittorrent por su interfaz web) queda aparcada en favor de
      libtorrent en el .venv, que es lo que se acordó el 2026-10-03. Ver H59.

## H59 · Ver un torrent mientras se descarga — ADR-106 · [x]
Diseño acordado en la conversación del 2026-10-03, que **no estaba escrito aquí** (error mío: lo di por escrito al
recapitular). libtorrent 2.1.1 tiene ruedas oficiales en PyPI (BSD) para cp312 en Linux x86_64/aarch64, musl,
Windows 32/64 y macOS arm64/x86_64, y su API se comprobó completa con el Python del proyecto (`set_piece_deadline`,
`have_piece`, `piece_priority`, `file_priority`, `set_sequential_download`, `clear_piece_deadlines`).
- [x] L1 **Extra opcional y APAGADO por defecto**, como los servicios de nube: un reproductor no tiene por qué
      traer un cliente de torrents encendido. Se activa en Preferencias y se dice en una línea qué implica.
- [x] L2 **La ventana de reproducción manda**: `set_piece_deadline` sobre los trozos que hacen falta YA, no
      `set_sequential_download` a lo bruto, que pelea con el salto hacia delante y desperdicia la bajada.
- [x] L3 **mpvd sirve el fichero que crece** por HTTP local con Range, igual que ya sirve el fichero original de
      una sala (H44/C1): así mpv no necesita saber nada de torrents y los saltos funcionan dentro de lo bajado.
- [x] L4 **Al acabar se deja de sembrar**; seguir sembrando es un interruptor explícito, no el comportamiento.
- [x] L5 **Trackers**: `trackers_best` (20, de ngosang/trackerslist) en caché semanal con copia incluida en el
      repo como respaldo, y **nunca** en un torrent privado (`priv()`), que es motivo de expulsión. **Sin
      blocklist**: la del nivel 2 bloquea rangos enteros por reputación, rompe conexiones legítimas y hay que
      mantenerla; no paga.
- [x] L6 **Privacidad por interruptor**, no por defecto: SOCKS5 y `anonymous_mode`, con las credenciales en
      `~/.config/mpv-uos/` con permisos 600 y fuera del repo. Lo demás se queda como lo trae libtorrent (DHT, LSD,
      cifrado 1, 200 conexiones), que son valores sensatos y probados.
- [x] L7 **Sin estrangular la subida**: un cliente que no devuelve nada es un cliente que no baja.
- [x] L8 **Entrada por la puerta única**: un magnet pegado en `ctrl+o` se reconoce y se ofrece ver o descargar,
      como cualquier otro enlace. Nada de un sitio nuevo.
- [x] L9 **Tests sin red**: un sembrador y un cliente locales en el mismo equipo, como se probó el relay.
- [x] L10 **Medido** en este portátil, con un sembrador local: **12,1 ms de CPU por MB** descargado (191 MB a
      68 MB/s costaron 2,30 s de CPU) y **110 MB** de RSS. O sea, bajando a 5 MB/s —una conexión doméstica buena—
      el torrent cuesta un **6 % de un núcleo**, encima del 24 % que ya gasta el reproductor. Crear el .torrent de
      un fichero de 200 MB: 0,9 s. Y el **aviso legal** sale la primera vez que se abre uno: la herramienta es
      neutra, lo que se baje es responsabilidad de quien lo baje.
- [x] L11 (añadido al construirlo) Los tests **no pueden escribir en la configuración de verdad**: el interruptor y
      las credenciales del proxy viven en `~/.config/mpv-uos/`, y la primera pasada del test dejó ahí un
      `torrent.json` con los torrents ENCENDIDOS. Ahora `MPV_UOS_CONFIG_DIR` apunta a la carpeta del test, como ya
      hacían el resto de rutas.

## H65 · Un menú cerrado no se reabre solo — ADR-104 · [x]
Encontrado por la batería completa el 2026-10-03, y era un fallo de verdad y no del test: el test esperaba 30 s a
que el menú de TV se cerrase y seguía abierto.
- [x] Q1 Cada vista pinta **dos veces** —las filas de «cargando» al entrar y las de verdad cuando contesta mpvd—.
      Si entre las dos se cerraba el menú, el segundo pintado lo **volvía a abrir**: `show()` abría siempre que
      uosc no tuviera ya ese menú. Y el reinicio de la navegación va con 0,2 s de retardo a propósito (uosc pasa
      por `nil` al sustituir un menú), que es justo la ventana por la que se colaba.
- [x] Q2 Ahora **abrir hay que haberlo pedido**: `open_view` da permiso (`opening`) y lo consume el primer pintado
      que abre; el permiso caduca en el instante en que no hay menú, sin esperar al retardo. Una respuesta que
      llega tarde, como mucho, actualiza un menú que siga abierto. El permiso **no** se apaga al volver de
      `open_view`, porque hay vistas que solo pintan desde su callback y entonces el primer pintado llega después.
- [x] Q3 El detector es el test que ya existía (`test_mu_iptv_tracks`, la espera de que el menú se cierre): fallaba
      con el fallo puesto y pasa con él quitado, y con él los veinte tests de TV.

## H64 · Explorar las carpetas del equipo — ADR-103 · [x]
Pedido por Ser el 2026-10-03 pensando en la Raspberry conectada al televisor: «se pueden abrir las carpetas del
dispositivo y puede cargar el contenido de las deseadas». Lo que había para añadir una carpeta era **teclear la
ruta**, y en un salón no hay teclado.
- [x] P1 `files.places` y `files.browse` en mpvd (`mpvd/files.py`), y nada más: las puertas por donde empezar
      (Vídeos, Música, Descargas, Imágenes, las carpetas de la biblioteca, **las unidades conectadas** —un pincho
      USB en la Pi sale solo— y la carpeta personal) y el listado de una carpeta. No indexa, no recorre
      recursivamente y no recuerda nada: para eso está la biblioteca; esto tiene que contestar al instante.
- [x] P2 **Reproducir una carpeta entera no se construye**: mpv 0.41 abre directorios él mismo
      (`--directory-mode`, `--directory-filter-types`, comprobado contra el mpv instalado), así que se le pasa la
      ruta y monta la lista. Comprobado en el test: `playlist-count` 2 al abrir una carpeta con dos vídeos.
- [x] P3 Vista «Explorar carpetas» en *Biblioteca*, en *Carpetas* («Buscarla explorando el equipo…», sin teclear)
      y en la **puerta única** con la caja vacía. Las subcarpetas van primero, en orden natural («Capítulo 2» antes
      que «Capítulo 10»), con cuántas cosas útiles hay dentro; los archivos, con su tamaño.
- [x] P4 **Pensado para un mando**: lo que se puede hacer con una carpeta son FILAS y no acciones de Tab, porque el
      mando de la tele no tiene Tab; y hay una fila «Subir», porque en ese mando «atrás» cierra el menú (es
      `close`) y no sube un nivel.

## H63 · La batería no es de fiar del todo (encontrado el 2026-10-03) — [ ]
Cuatro pasadas completas seguidas dieron 864, 867, 868 y 867 pasando, y en cada una falló **una pareja distinta**
de tests, nunca los mismos y nunca nada de lo recién construido. Eso no es un programa roto: es una batería que no
es de fiar, y una batería que falla al azar es una batería que se deja de leer — que es exactamente cómo se colaron
los once fallos de H55.
- [x] N0 Arregladas de raíz las causas que fueron apareciendo: el estado que se publica antes que sus filas
      (test_books, test_mu_subs), esperas de 10 s para cosas que hace mpv (un SRT externo, cerrar un menú), marcar
      con el vídeo corriendo (test_mu_cut) y leer `chapter-list` justo después de marcar. Y dos expectativas
      equivocadas: una copia muerta puede explicar su muerte de varias maneras, y `muertas[0]` no tiene un orden
      garantizado.
- [x] N1 **Barrido hecho, y por la vía buena: en los scripts, no en los tests.** En vez de repasar sesenta esperas
      una a una, `open_view` **vacía las filas** al cambiar de vista en los trece scripts que las publican, así que
      es **imposible** leer las de la vista anterior. `mu-av` ya lo hacía desde antes —con el comentario puesto— y
      funcionaba: era aplicar el mismo patrón. El efecto buscado es que lo que antes pasaba en silencio ahora falle
      a la cara, y falló: cuatro tests leían filas viejas (`test_mu_convert`, `test_mu_feeds`, `test_mu_library`,
      `test_mu_music`). Arreglados los cuatro, y de paso dos helpers que **reventaban dentro de un
      `wait_property`** —un `next()` sin defecto— y tumbaban la espera entera en vez de volver a mirar.
- [ ] N2 **Y medir**: tres pasadas seguidas sin un solo fallo antes de declararlo. Mientras eso no ocurra, lo
      honesto al cerrar una tanda es decir el número y qué falló, no «check en verde».

## H62 · Ver y saber lo que pasa por detrás — ADR-102 · [x]
De la prueba de Ser: «debe haber alguna forma de monitorear los procesos en segundo plano, cualquiera… se debe
poder ver el proceso en algún sitio, y debe avisar al terminar».
- [x] N1 **Guardar un tramo no recodifica, de fábrica.** El formato por defecto era MP4, o sea recodificar, y Ser
      daba por supuesto lo contrario. Ahora es «Sin recodificar»: copia los flujos tal cual (0,07 s para un corte
      de 6 s frente a 1,16 s, y sin perder un bit). Unir varios en uno sí obliga —pegar trozos es un filtro—, y la
      lista lo dice antes de pulsar y señala «Formato».
- [x] N2 **«Tareas» enseña TODO lo que pasa por detrás**, no solo conversiones y descargas: también los subtítulos
      con IA, la traducción, el índice por temas, la intro, la música… Todo eso ya pasaba por la misma cola de
      trabajos del servidor, pero no se publicaba. Con dos cuidados: el nombre interno (`asr.model.small`) se
      traduce a castellano, y un trabajo solo sale si es pesado o si lleva más de 2 s, porque un indicador que
      parpadea con cada chapucilla de 50 ms se aprende a ignorar. Una conversión no se cuenta dos veces.
- [x] N3 **Avisa al terminar** con un aviso del escritorio, que es lo que se ve con el reproductor detrás o
      cerrado. Solo si ha tardado más de 20 s (avisar de algo que acabó delante de ti es ruido) y siempre si ha
      fallado. Se calla con `MPV_UOS_NO_NOTIFY`.

## H61 · El mando del televisor (HDMI-CEC) en la Raspberry — ADR-102 · [x] falta probarlo en la Pi
Revisa el juicio de `docs/IDEAS.md` 1.8, que lo daba por poco valioso pensando en un portátil. En una Pi conectada
a la tele no hay teclado, y el mando del televisor es el único que ya tiene en la mano quien está mirando.
- [x] M1 **Puente `cec.*` en mpvd**, con la forma que ya tiene el del gamepad (`mpvd/gamepad.py`): lee el aparato,
      traduce a un verbo y se lo manda por IPC al mpv de la sesión. El vocabulario de acciones **es el mismo que ya
      existe** (`play_pause`, `back`, `forward`, `prev`, `next`, `volume_up`, `volume_down`, `subtitles`, `menu`,
      `close`), así que no hay que inventar ni mantener un segundo mapa.
- [x] M2 **Correspondencia de teclas CEC** (códigos de «user control»): 0x00 Select → aceptar, 0x01/0x02 arriba y
      abajo, 0x03/0x04 → atrás y adelante, 0x44 Play y 0x46 Pause → play_pause, 0x45 Stop → cerrar, 0x48/0x49
      rebobinar y avanzar, 0x0D Exit → cerrar, 0x09/0x0A (menú raíz y de ajustes) → el menú, 0x41/0x42 → volumen.
- [x] M3 **Probado sin hardware**, como el gamepad: `MPV_UOS_CEC_DEVICE` apuntando a un fifo que escribe las mismas
      líneas que `cec-client`, y un test que comprueba que cada tecla acaba en su acción. La última milla (que el
      televisor pase las teclas, lo que depende de que implemente «remote control pass through» y de que seamos la
      fuente activa) solo se puede comprobar en la Pi → NEEDS_HUMAN.
- [x] M4 **Apagado si no hay CEC**, sin ruido: en un equipo sin `/dev/cec*` el servicio no se anuncia en
      `capabilities`, igual que ocurre con el gamepad y con `fpcalc`.
- [x] M5 **No se lee `cec-client` sino el aparato del kernel** (`/dev/cec0`), que es lo que cambió el diseño al
      construirlo: es una interfaz binaria documentada, no hace falta instalar nada, y es la misma forma que el
      joystick que ya lee el gamepad. Las constantes y el tamaño de `struct cec_msg` salen de
      `/usr/include/linux/cec.h` comprobados con ctypes, no inventados.
- [x] M6 **Manda siempre**, no solo en «modo salón» como el gamepad: es un mando físico y si sus teclas llegan es
      porque alguien las ha pulsado. Y no hay que pedirlo: el reproductor lo enciende solo si el equipo tiene CEC.
- [~] M7 **La última milla, en la Pi**: que el televisor pase sus teclas. Órdenes en NEEDS_HUMAN.md. Mientras,
      siguen los dos mandos que ya funcionan: el del móvil (`alt+z`) y un gamepad en modo salón.

## H27 · Diferenciales
- [x] Mini reproductor flotante, modo salón (letra grande, gamepad; HDMI-CEC no: necesita hardware y libcec), modo sencillo.
- [x] «¿Qué me he perdido?» con modelo local (extractivo, ADR-062).
- [x] Enviar a la tele por DLNA (Chromecast fuera por ahora: ver ADR-063 y docs/PLATAFORMAS.md).

## H28 · Plataformas
- [x] Paquete Linux (AppImage y/o Flatpak), prueba en ARM64 (Raspberry Pi 5 / modo salón), Windows (named pipes en mpvd, lanzador,
      yt-dlp.exe, whisper, instalador) y macOS (.app). Lo que no se pueda probar sin el hardware, a docs/PLATAFORMAS.md y NEEDS_HUMAN.md.
      Hecho: AppImage x86_64, .app de macOS, named pipes, bin/mpv-uos.ps1 y tools/install.ps1 (probados con pwsh 7 en Linux);
      ARM64, Mac y Windows reales sin hardware → NEEDS_HUMAN.md.

## H34 · Revisión de calidad · SOLO en una iteración con effort xhigh
Puerta: antes de empezar H34 mira la última línea «▶ iteración» de logs/runner.log. Si NO dice `effort=xhigh`, no empieces:
escribe en PROGRESS.md la línea exacta `H34 espera iteración xhigh` y termina la iteración (sin ESTADO_GLOBAL: COMPLETADO);
un vigilante relanzará el runner en xhigh. En xhigh, borra esa línea de PROGRESS.md y adelante (modo a tope, subagentes).
- [x] Revisión de código por áreas de H15–H33 con subagentes (errores reales, integración entre módulos, textos y comportamiento
      coherentes, rendimiento en este portátil). Siete revisiones (TV, descargas/convertir, subtítulos, biblioteca/suscripciones/notas,
      audio, compartir/mando, núcleo/UI). Integración Lua↔mpvd verificada entera: los 175 métodos que llama el Lua existen en los 244
      de mpvd y los parámetros cuadran (ahora es un test, `tests/test_integracion_lua_rpc.py`).
- [x] Cada hallazgo confirmado, corregido con su test (42 en total, commits «H34 (1)»…«H34 (10)»). Lista de los
      confirmados leyendo el código (ordenados por gravedad):
      · Notas: `render()` borra el texto que el usuario escriba en el `.md` (y `_scan()` reescribe un `.md` ajeno de la carpeta);
        «Exportar a una carpeta» sobrescribe sin avisar un fichero del usuario; `safe_name` recorta por caracteres, no por bytes.
      · Subtítulos IA: la pre-rodadura no se descarta → frase duplicada en cada frontera y cues con fin < inicio; la pista de audio
        no entra en la identidad de la tarea ni en la caché (VO/doblaje se mezclan); `_adopt_model` lanza `ValueError` con modelos
        `*.en`; todas las traducciones de subtítulos de internet van al MISMO archivo; el panel no olvida `state.web` al cambiar de
        vídeo; `translate.py` ignora los hilos pedidos en máquinas de ≤2 núcleos (paréntesis).
      · Descargas: cancelar una que aún no ha empezado la deja «en cola» para siempre y se relanza al reiniciar; «Instalar yt-dlp»
        manda un parámetro que el método no acepta (nunca funciona); un lote >200 enlaces pierde de la lista los primeros; al parar
        mpvd la cola se marca «cancelada» en vez de reanudarse (H19); las descargas ocupan los workers del JobQueue y anulan las
        prioridades; en «Descargar» faltan las etiquetas «fluido en tu equipo» (H31).
      · Grabar: `save_range` ignora la ruta guardada → al cambiar de archivo recorta el equivocado o pierde el tramo; dos dueños de
        `stream-record` (mu-record y mu-iptv, con carpetas distintas) → punto rojo pegado y grabación cortada en silencio; el tope
        de 600 s de `study.clip` hace fallar cualquier grabación local de más de 10 min; la carpeta de reserva lleva «MPV-UOS» a pelo.
      · Menú: «Recientes» se reabre solo si se cierra con Esc mientras espera a mpvd; en la paleta `⌫` no cierra nada (cierra el
        tipo de menú equivocado).
      · TV: un fallo blando al bajar el EPG deja la guía en bucle infinito de refrescos; una excepción en el bucle del programador
        mata todas las grabaciones programadas sin aviso; `_rebuild_index` bloquea el bucle de eventos una vez por fuente; errores de
        mpvd/ffmpeg en inglés dentro de menús en español; los subtítulos externos cuentan como CC del canal y un informe «player»
        pobre borra el resumen «master»; carrera en el memo de emparejamiento del EPG.
      · Biblioteca y suscripciones: quitar una carpeta borra las filas de una subcarpeta que sigue en la biblioteca; renombrar una
        suscripción deja huérfanos los ficheros ya movidos y borra sus registros; la extracción de carátulas reintenta ffmpeg en
        cada escaneo cuando falla.
      · Compartir y mando: al cerrar la sala quedan ffmpeg huérfanos y se recrea la carpeta borrada; `remote.status` bloquea el bucle
        de eventos con `subprocess.run`; el volumen de la tele se manda en absoluto desde un valor inventado; 20 fallos acumulados
        (sin caducar) inutilizan el enlace de la sala; en sala privada los invitados desconectados no sueltan la plaza; el 500
        genérico devuelve el texto de la excepción (y los `limit` de la API no se validan).
- [x] Pruebas de extremo a extremo headless de los flujos principales: ya había una por módulo (test_mu_*.py, todas con mpv
      y mpvd reales); H34 añade lo que faltaba, que era cruzar módulos en una misma sesión: `tests/test_flujos_e2e.py`
      (saltar de un módulo a otro con su tecla sin pasar por «Atrás», y que nota + preferencia + posición sigan ahí tras
      reiniciar mpv contra el mismo daemon) y `tests/test_integracion_lua_rpc.py`. `tools/check.sh` completo: ver PROGRESS.md
      (la máquina de Ser no estaba libre: su llama-server ocupaba ~2,5 de los 4 núcleos).
- [x] Documentación final al día (README, USO, ATAJOS, PLATAFORMAS) y resumen para Ser en PROGRESS.md: qué probar a mano y cómo.
Solo cuando H34 esté hecho: `ESTADO_GLOBAL: COMPLETADO`.

## Ampliación aprobada por Ser el 2026-10-01 (H35–H40), a partir de su uso real
Decisiones de Ser que mandan sobre lo escrito antes: **fuera los subtítulos IA en vivo** (solo los del canal en TV y radio);
**resúmenes solo donde la transcripción es gratis** (YouTube y webs, o un archivo local ya transcrito); **por defecto el mejor
modelo que vaya más rápido que el vídeo**, y los más lentos como «máxima calidad» con el tiempo calculado delante; **al cerrar
un vídeo se pregunta** qué hacer con lo que quede trabajando, salvo las grabaciones programadas. Cada hito cierra con
`tools/check.sh` en verde y su ADR cuando cambia una decisión anterior.

## H35 · Arreglos de uso y la barra como mando
- [x] A1 El botón ● de grabar para la grabación en el segundo clic, sin abrir el menú (hoy abre el menú siempre, mu-record:178).
- [x] A2 El QR de la sala se puede quitar: no rearmar el temporizador de 120 s en cada refresco, fila del menú que alterna de
      verdad y tecla asignada (mu-share:759 y :749, input.conf:103).
- [x] A3 Recuperar en docs/BENCHMARKS.md la sección de los trozos de 28,5 s que se perdió al regenerar el fichero.
- [x] A4 Cuadrar la recomendación por tier que imprime tools/bench_asr.sh con la de mpvd/asr/models.py (hoy discrepan).
- [x] B1 Botón «solo audio» en la barra de uosc (la función ya existe en alt+a y cubre internet y archivos locales).
- [x] B2 El icono de subtítulos de la barra abre el panel de mu-subs, no la lista de pistas de uosc.
- [x] H1 La página del invitado dice «Preparando la retransmisión… N s listos» y el anfitrión ve ese mismo progreso.

## H36 · Subtítulos: sencillos, buenos y sin esperas en vivo
- [x] C1 Quitar los subtítulos IA en vivo: pasada por lotes con ventaja (ADR nuevo que sustituye a los de ASR en vivo). En TV
      y radio, solo los del canal. Desaparecen el look-ahead y el troceado según la posición.
- [x] C2 Por defecto small-q8_0 (medido: ritmo 0,45; único bueno más rápido que el vídeo). medium-q5_0 (4,03) y
      large-v3-turbo-q5_0 (5,22) como «máxima calidad» explícita. Elección por tier: en equipos con más núcleos, los buenos.
- [x] C3 El aviso de tiempo se calcula con el ritmo medido (AsrTask.rtf, que ya existe) y sobre lo que queda: «listos en 4 min
      y no te alcanzará» si el ritmo < 1, o el total más la alternativa con su número si el ritmo ≥ 1. Se recalcula si la
      máquina se carga.
- [x] C4 Alta guiada de OpenSubtitles en dos pasos (abrir la página de la clave · pegarla del portapapeles) cuando un archivo
      no trae subtítulos, y mostrar la cuota que queda.
- [x] C5 Cascada de proveedores: incrustados → canal/web → OpenSubtitles → Subdl → Podnapisi, mezclados y ordenados por
      fiabilidad (hash antes que título). Verificar las dos APIs nuevas contra el servicio real antes de ofrecerlas.
      **Comprobado el 2026-10-01 y por eso cambia**: Podnapisi está muerto (su dominio da NXDOMAIN) y Subdl necesita una
      clave gratuita (403 sin ella) → anotado en NEEDS_HUMAN.md. Quedan dos proveedores reales y el menú dice por qué
      falta cada uno (ADR-071). Añadir un tercero es rellenar una tupla y su `pick`.
- [x] C6 Traducir cualquier pista (incrustada, descargada, de la web) a español, inglés y francés, con OPUS-MT por defecto.
- [x] C7 Revisar lo que dependía de los subtítulos en vivo (modo estudio).
- [x] C8 Al cerrar un vídeo, preguntar qué hacer con lo que quede trabajando **de ese archivo** (subtítulos, intro, índice,
      traducción), diciendo que lo hecho se guarda y continuará. Nunca se pregunta ni se para una grabación programada. Los
      subtítulos preguntan siempre; descargas y conversiones admiten «no volver a preguntar». Sin ventana, aviso de escritorio
      con «Parar» y recordatorio al abrir. Y una línea en el menú que diga siempre qué se está haciendo por detrás.
- [x] B3 Panel de subtítulos en tres bloques: lo que ya hay · buscar en internet · crear con IA (al final), con tamaño y
      retraso siempre a mano.

## H37 · Descargar: una sola puerta
- [x] D1 Un único «Descargar…»: una caja donde pegar un enlace o veinte, un canal o una lista de reproducción.
- [x] D2 Lista con casillas; la primera fila fija el formato común («Para todos: Audio · Opus 128») y cada fila puede
      sobrescribirlo con Tab.
- [x] D3 Casilla SRT global y por fila.
- [x] D4 Guardados de Instagram y TikTok (perfil/colección) con la opción de cookies del navegador, que ya existe desactivada.
      Verificar antes contra yt-dlp que el extractor lo soporta; si no, decirlo en el menú en vez de fallar.
      **Comprobado el 2026-10-01 contra el binario instalado** (preguntando a sus extractores): los guardados de Instagram
      y los favoritos de TikTok NO tienen extractor → el menú lo dice con su alternativa; el perfil de Instagram existe pero
      yt-dlp lo marca roto → se ofrece avisando; perfil y colecciones de TikTok sí funcionan (docs/YTDLP.md §12).

## H38 · Resumen con enlaces al minuto
- [x] G1 Solo donde la transcripción es gratis: YouTube y webs con subtítulos, o un archivo local que ya esté transcrito.
      Nunca se lanza una transcripción para resumir.
- [x] G2 Nivel 1 sin IA generativa: secciones por significado y frases clave con su minuto exacto, instantáneo (amplía recap).
- [x] G3 Nivel 2 con modelo local: prosa corta o larga en español, inglés o francés, escrita a partir del nivel 1.
- [x] G4 Cada marca [mm:ss] se valida contra el SRT; si no existe se ajusta a la frase más parecida y, si nada encaja, se
      quita antes que mentir.
- [x] G5 El resumen es un menú: cada viñeta salta a su minuto.
- [x] G6 Modelo descargado al pedirlo (fijado por SHA-256) y binario de llama.cpp vendorizado como whisper.cpp, con casilla
      opcional en el instalador. Modelo concreto: el que gane el banco de pruebas del 2026-10-01.
- [x] ADR nuevo que sustituye al ADR-062 (que dejaba fuera el LLM a propósito): Ser acepta el coste y el nivel 1 se queda
      como respuesta instantánea.

## H39 · TV, radio e intro
- [x] E1 Radio: salto automático al siguiente espejo que suene (la lista trae «Cadena SER ×6» y los primeros suelen estar
      caídos); el mecanismo ya existe para TV.
- [x] E2 La lista muestra lo que dijo la comprobación de canales, en vez de dejar probar a ciegas.
- [x] E3 SponsorBlock al reproducir (hoy solo al descargar), con la API que no envía el id del vídeo sino un prefijo de su
      hash, y con interruptor.
- [x] E4 Intro: calcular las huellas de los siguientes episodios en el momento correcto, parar en cuanto haya coincidencia
      clara y tres estados visibles en el icono (buscando / saltar / no hay). La ventana de 10 min ya está bien.

## H40 · Despertar para grabar y apagar al terminar
- [x] F1 Al programar una grabación: «despertar 5 min antes» y «al terminar: nada / suspender / apagar». Solo desde
      suspensión, no desde apagado.
- [x] F2 Inhibir el sueño mientras graba (systemd-inhibit / caffeinate / SetThreadExecutionState).
- [x] F3 Tres seguros antes de suspender o apagar: nadie usando el equipo, ninguna grabación cerca, nada descargando o
      convirtiendo; y aviso de 60 s cancelable.
- [x] F4 Linux y macOS: una instalación con sudo, una sola vez, limitada a rtcwake / pmset schedule → NEEDS_HUMAN.
- [x] F5 Windows: tarea programada con WakeToRun sin administrador, comprobando que el plan de energía permite los
      temporizadores de activación y avisando si no. Sin Windows aquí: queda sin probar en real.

## H41 · Nombre — **Atalaya Player** (decidido por Ser el 2026-10-02)
- [x] `brand.json`: `name` = «Atalaya Player», `folder` = «Atalaya». `id` se queda en `mpv-uos` a propósito (sockets,
      carpeta de datos, orden): renombrarlo obligaría a migrar los datos del usuario sin ganar nada. Para teclear,
      `tools/install.sh` instala además un segundo nombre, `atalaya`, enlazado al lanzador.
- [x] Los tests leen el nombre y la carpeta de `brand.json` (antes «MPV-UOS» estaba literal en 30 ficheros, la mayoría
      en las migas de los menús), así que el próximo cambio de nombre no vuelve a tocarlos.
- [x] Movidas `~/Vídeos/MPV-UOS` → `~/Vídeos/Atalaya` y `~/Música/MPV-UOS` → `~/Música/Atalaya` (285 ficheros).
      El AppImage y los logos toman el nombre de la marca; el nombre del fichero sale de `folder`, que no lleva espacios.

## H47 · La lista de reproducción al abrir varios archivos
- [x] Abrir varios de golpe ya construía la lista (lo hace mpv, y el `.desktop` pasa `%U`), pero no se veía: lo único
      que lo insinuaba eran los botones ⏮⏭ de la barra. Ahora se enseña al abrir el primero y se quita sola a los 4 s
      (`mu-menu-playlist_on_open`, 0 la desactiva). No sale con un solo archivo ni al cambiar de pista.

## H48 · Consumo — medido, nada que hacer (2026-10-02)
- [x] Medido en este portátil: en pausa 0,1 % de CPU y mpvd 0,00 %; reproduciendo, +6 puntos de un núcleo (1,5 % del
      total) y +13 MB sobre mpv pelado. uosc es gratis (13,7 % frente a 14,1 % sin él) y nuestro `mpv.conf` es más
      barato que el mpv de serie. Ningún temporizador desbocado: los rápidos se crean solo cuando hacen falta.
- [ ] **No-objetivo**: un modo ligero o una configuración mínima paralela. Decidido con Ser el 2026-10-02: lo que se
      ganaría es RAM, no fluidez (los scripts no están en el camino del vídeo), y costaría una segunda configuración
      que mantener y el doble de tests. Si alguna vez molesta, el camino es bisecar qué script se lleva los 9 puntos
      y arreglar ese, no construir dos reproductores.

## H49 · Idiomas: castellano, inglés y francés (diseño: docs/IDIOMAS.md)
Regla de Ser: sistema en castellano o francés → ese idioma; inglés o cualquier otro → inglés. Medido antes de
empezar: ~1.800 cadenas visibles (1.151 en Lua, ~520 en mpvd, ~56 en el JS de las páginas), o sea ~3.600 traducidas.
- [x] G1 Maquinaria: `locales/en.json` y `fr.json` (la cadena castellana ES la clave, así que no hay `es.json` y lo
      no traducido cae al castellano), `mu/i18n.lua`, `mpvd/i18n.py`, `tools/i18n_extract.py` y la detección en los
      **dos** lanzadores (bash y PowerShell), que pasan `uosc-languages` (uosc ya está traducido, no se duplica) y
      `mu-core-lang`. OJO: cada script de mpv tiene su propio estado Lua, así que el idioma lo lee el módulo de
      `options/script-opts` en cada script; que lo fijara mu-core no servía de nada.
- [x] G2 Preferencias → Idioma (automático / Castellano / English / Français), que gana al idioma del sistema. Lo
      leen los dos lanzadores de `prefs.json` antes de arrancar mpv; se aplica al reiniciar y la fila lo dice. Un
      valor imposible o un `prefs.json` roto se ignoran sin ruido y se vuelve al idioma del sistema.
- [x] G3 Lo primero que se ve: mu-menu (menú, paleta, ayuda, inicio, preferencias) y mu-modes, con **196 cadenas**
      traducidas a inglés y francés y un test que arranca el reproductor en los tres idiomas.
- [x] G4 Los módulos grandes: ytdl, iptv, subs, record, share, library, music.
- [ ] G5 Los mensajes de mpvd que salen en el OSD.
- [ ] G6 Las páginas servidas (sala, mando, descargas), que siguen al **navegador del invitado**, no al sistema del
      anfitrión: las abre otra persona y puede estar en otro idioma.
- [ ] G7 Repaso de las traducciones (el francés, con ojo).

## H50 · El sitio web del proyecto (solucionesconscientes.es/atalaya)
- [ ] F1 La página pública: qué es, las características importantes, capturas, cómo instalarlo. Para alguien que no
      conoce el proyecto y decide en treinta segundos si le interesa.
- [ ] F2 La página exhaustiva (GitHub o donde convenga): absolutamente todos los detalles, las tecnologías que usa,
      la arquitectura, las decisiones y sus porqués.

## H42 · Una sola puerta para abrir y descargar (análisis: docs/ANALISIS-SALA-E-INTERFAZ.md §B.2.3) — ADR-077
- [x] A1 Una entrada «Abrir o descargar» que acepte cualquier cosa: un enlace, varios enlaces pegados, una lista de
      reproducción, un canal entero, una ruta local o lo que haya en el portapapeles, sin que el usuario tenga que
      saber de antemano qué es. (`mu-ytdl` vista `gate`, tecla `ctrl+o`; también carpetas y `.txt` de enlaces, y la
      extensión y el tamaño se miran ANTES de leer el archivo.)
- [x] A2 Después de reconocerlo, una sola pregunta: reproducir o descargar. La pantalla de descarga actual se queda
      como está (parámetros para todos, con la posibilidad de cambiar algunos vídeos uno a uno). Dice cuántos
      elementos hay; a mpvd solo se le pregunta si la URL huele a lista o canal, y la respuesta se reutiliza.
- [x] A3 Retirar las otras cinco puertas (Abrir archivo, Abrir URL…, Pegar URL, Buscar en YouTube, Suscripciones)
      del primer nivel; siguen existiendo como atajos de teclado y dentro de la nueva entrada.
- [x] A4 Botón de copiar en todo enlace que haya que llevarse a otro aparato: sala, mando desde el móvil, panel de
      descargas. Con aviso en el OSD de que se ha copiado. (Un único `mu/clip.lua`: antes el mismo código estaba
      escrito tres veces y solo una tenía respaldo sin backend de mpv.)

## H43 · Grabar: formato, programación visible y radio (§B.2.4-6) — ADR-079, ADR-080
- [x] B1 Fila «Formato» en el menú de grabar, que se recuerda: igual que el original (sin recodificar) / MP4 /
      solo audio en Opus 128. Dice de antemano si los códecs caben en MP4 y, si no, avisa y graba en MKV. Al dejar de
      ser una pregunta, «solo el audio» deja de ser una fila aparte (seguía siendo la misma elección dos veces).
- [x] B2 «Programar una grabación» visible en el primer nivel de TV y radio y también en Grabar, no solo con Tab
      dentro de la lista de un canal.
- [x] B3 La radio se puede programar. OJO: de las dos condiciones que apuntaba el análisis solo una lo impedía (la de
      `views.sched_new`); la de `want_now` es el filtro de la guía de TV, que la radio no tiene, y debe quedarse.
- [x] B4 Al volver a pulsar el botón de grabar, termina sin preguntar nada (ya hecho en H35; comprobado con la fila
      de formato nueva: el botón sigue llevando a `record-toggle` mientras se graba).

## H44 · La sala: fichero original y el reproductor del invitado (§A) — ADR-082, ADR-088 · [x] falta C8 (menor)
- [x] C1 Handler Range que lee por trozos (256 kB) en vez de `path.read_bytes()`. El test sirve 300 MB enteros y mide
      el RSS del proceso. El comportamiento del Range se conserva exactamente.
- [x] C2 Ruta `GET /s/<sala>/file` con el fichero original; la ruta real no se publica (`_media_public` quita
      `path` y `local`).
- [x] C3 Bloque «Abrir en mi reproductor»: copiar el enlace, `.m3u` (`GET /s/<sala>/file.m3u`), la línea
      `mpv "<enlace>"` y la posición del anfitrión con su botón. mpv y VLC no mandan la cookie, así que el enlace
      lleva la credencial firmada del invitado en la query (`?k=`), pedida a `api/filelink`. Comprobado en un
      navegador real (test_share_browser).
- [x] C4 El camino se decide sin preguntar, mirando códecs, contenedor **y dónde está el `moov`**: «es un MP4» no
      vale. El original siempre para «tu reproductor»; para el navegador, el original si puede con él y el relay si
      no. El remux `-c copy` a fMP4/WebM queda como mejora de CPU (ver abajo), no como arreglo: con C5 la espera ya
      no es el problema.
- [x] C5 El relay empieza donde está el anfitrión (`-ss` antes del `-i`), el stream recuerda el `offset` y la página
      lo descuenta y lo dice («Empezamos donde va el anfitrión»). En un directo no se pone `-ss`.
- [x] C6 Unirse a la sala desde Atalaya Player (ADR-088): `mpvd/share/guest.py` entra, abre el SSE y sigue pausa,
      saltos y velocidad con **la misma cuenta que la página** (`sync.js`, portada con test que compara las dos).
      El invitado reproduce el **fichero original** (`?k=`), no el relay: calidad original, saltos instantáneos y
      cero CPU del anfitrión. El enlace entra por el lanzador (`bin/mpv-uos <enlace>`, también en Windows), por la
      puerta única de H42 y por *Compartir → Entrar en una sala de otro…*. Las rutas `media/` y `subs/` aceptan la
      credencial en la query, porque un reproductor de verdad no manda cookies.
- [x] C7 ADR: WebTorrent como no-objetivo, con los números (ADR-082).
- [~] C8 **Descartado al medirlo** — ADR-099. La premisa era falsa: el relay **ya** copia cuando los códecs lo
      permiten (comprobado, `plans_for` devuelve modo `copy` con `-c:v copy -c:a copy` para un MKV de H.264+AAC),
      así que no había nada que recodificar de lo que ahorrar. Remux a MP4 frente al relay HLS del mismo fichero:
      0,08 s los dos, y 1.690.840 bytes frente a 1.814.052 (un 7 % menos, por la cabecera de MPEG-TS). Donde el
      relay sí recodifica —HEVC + Opus, que es lo que graba esta casa— un remux no sirve, porque eso no lo abre
      ningún navegador; ese caso ya lo cubre C6 (el invitado usa su propio reproductor con el fichero original).

## H51 · Compartir, de la prueba de Ser (2026-10-02) — ADR-089 · [x]
- [x] D1 La sala sale a internet **por defecto** (compartir es con quien no está en casa) y `tools/vendor.sh`
      instala ya `cloudflared`: un ajuste encendido que no puede cumplirse es peor que no tenerlo.
- [x] D2 **El enlace se copia solo** al crear la sala, con aviso. El QR deja de plantarse en la pantalla y pasa a
      ser una fila del menú y `alt+Q`, para cuando quien entra está delante.
- [x] D3 **El minuto de espera era un enlace muerto**: cloudflared da la dirección a los 5,6-7,7 s y no enruta hasta
      65-69 s (medido tres veces). La sala se abre ya con su dirección local, el túnel va detrás, y la pública se
      sondea hasta que contesta; solo entonces se copia. Si no contesta en 4 min, se dice.
- [x] D4 **El invitado sigue los cambios de película**, en el navegador y en Atalaya Player. Dos fallos: la URL del
      archivo era la misma cadena para todas las películas (ahora lleva testigo por fichero) y `useRelay` no volvía
      nunca a false en la página, dejando la película anterior puesta para siempre.
- [x] D5 **Abrir en mi reproductor para todo**, no solo para un archivo del anfitrión: la TV y los vídeos de
      internet también. La lista HLS se reescribe con la credencial en cada trozo (si no, 401 en el primero).

## H52 · La barra y la línea de tiempo, de la prueba de Ser (2026-10-02) — ADR-090 · [x]
- [x] E1 `mu-marks`: **un solo dueño de `chapter-list`**, que es lo que uosc dibuja en la línea de tiempo. Guarda
      los capítulos propios de la película, mezcla las marcas y los repone. `mu-subs` (capítulos por tema) pasa por
      él en vez de escribir la propiedad a pelo.
- [x] E2 `mu-cut`: botón **✂ Tramos** con el número de tramos de insignia y botón **⟳ Bucle**. Marcar «desde aquí /
      hasta aquí» (`ctrl+x`); mientras eliges, A y B son las de mpv y uosc las dibuja sola; cerrado el tramo, queda
      pintado en azul de principio a fin.
- [x] E3 Guardar los tramos **sueltos** (un archivo cada uno) o **unidos** (uno con todos pegados, en una sola
      pasada de ffmpeg: `ranges` en el spec de conversión + `convert.cut`), en vídeo o **solo audio**.
- [x] E4 **Notas en la línea de tiempo**: botón **✎ Nota** y tecla `n` para anotar el minuto en el que estás; cada
      nota es un rombo con su texto. Las notas de *Estudio* también aparecen al momento.
- [x] E5 La barra, **agrupada por significado y ordenada por frecuencia**; «solo audio» sale de ella (sigue en
      `alt+a` y en el menú). Diseño y razonamiento en `docs/INTERFAZ.md`.

## H54 · Segunda prueba de Ser (2026-10-02) — ADR-091 · [x]
- [x] G1 Un salto fuera de lo que la retransmisión contiene la **rehace en esa posición**. Era la causa de «se
      queda en el mismo minuto y tampoco tiene play/pause»: hacia atrás no hay nada que enseñar y la corrección de
      deriva reposicionaba el vídeo sin parar. Con 3 s de gracia para no rehacerla a cada tirón.
- [x] G2 Una retransmisión **no se anuncia hasta que su lista existe** (si no, 404 al reenganchar).
- [x] G3 **`share.player_link`** y fila *Copiar el enlace para VLC o mpv*: el enlace de la sala no puede servir
      para un reproductor (su token va en el fragmento y el navegador no lo manda nunca). Probado con un mpv real.
- [x] G4 **Elegir qué tramos se exportan**, uno a uno; *unir* solo cuando hay más de uno elegido; las acciones de
      cada tramo pasan a los botones de su fila.

## H55 · Tercera prueba de Ser (2026-10-02) — ADR-092 · [x]
- [x] H1 Los invitados de una sala **privada entran pudiendo controlar** (`open_control`, con interruptor en el
      menú). Los mandos funcionaban —probado con un navegador de verdad—, lo que fallaba era que había que
      concederlos y eso estaba escondido. Con el control quitado, la página **dice por qué** están apagados.
- [x] H2 Al crear la sala se copian **los dos enlaces explicados** en un solo pegado, cuando la sala sirve de
      verdad. El de VLC no salía porque se pedía antes de que hubiera nada que compartir.
- [x] H3 Los tramos tienen **su propio icono** en la barra (con el número), se pueden **reordenar** con las flechas
      de cada fila, y **unir respeta ese orden** (se quitó la exigencia de que fueran en orden).
- [x] H4 Dos formatos nuevos: **Sin recodificar** (0,07 s para un corte de 6 s, sin pérdida; no puede unir) y
      **AV1** si el ffmpeg de la máquina lo trae. La lista sale de mpvd, que quita lo que no puede hacer.

## H56 · Lo que rompió H55, y lo que no se veía (2026-10-02) — ADR-093 · [x]
- [x] I1 El invitado «Reproductor» (credencial del enlace de VLC) pasa a ser `hidden`: no sale en la lista, no
      ocupa plaza y no se cuenta como espectador. Rompía la entrada en salas con tope.
- [x] I2 Guardar tramos **dice a dónde van** y ofrece «Ver cómo van en Tareas»; y si lo que se ve es la TV o un
      vídeo de internet, se dice **en el menú** que eso no se corta, con el camino que sí sirve.
- [x] I3 «Copiar los enlaces» es una fila del menú, para no depender de pillar la copia automática.
- [x] I4 La TV y la radio **arrancan solas**: `pause` es global en mpv y sobrevivía al cambio de canal.
- [x] I5 Seis tests adaptados al contrato nuevo de permisos (conservando el camino de pedir/conceder) y uno que
      fallaba **según la hora**: la guía intercala una fila de día cuando el siguiente programa cae tras medianoche.

## H57 · Programar que SUENE, no solo que grabe (2026-10-02) — ADR-094 · [x]
- [x] J1 Las programaciones tienen **modo**: `record` (lo de siempre), `play` (a esa hora se enciende y suena) o
      `both`. Reutiliza toda la maquinaria de H40: franja, despertador y suspender/apagar al terminar.
- [x] J2 **Si no hay reproductor abierto, se abre uno.** Es lo que da sentido a «a las 7:00 que suene la radio»:
      el equipo está suspendido, el despertador lo levanta y no hay ninguna ventana.
- [x] J3 Se puede programar **una canción, una carpeta, una lista o una dirección**, no solo un canal: se envuelve
      como canal de pega (`media_channel`) y hereda todo.
- [x] J4 Fila *Qué hacer en esa franja* en el menú de programaciones, recordada.
- [x] J5 **Programar una lista guardada** — ADR-098. Las listas con nombre ya existían (`music.playlists.*`,
      M3U8 en los datos del usuario) y el RPC ya aceptaba `media`: lo que faltaba era que se pudieran elegir y que
      una lista se cargara como lista. Ahora en *Programar* hay dos puertas más —«Una lista guardada…», que enseña
      las de Música, y «Lo que está puesto ahora»—, mpvd usa `loadlist` (no `loadfile`, que intentaría demuxear el
      .m3u8) y **la repite hasta que acabe la franja**, devolviendo al terminar el `loop-playlist` que hubiera. Con
      algo del disco el modo es «ponerlo» y el menú lo dice en el título, porque grabar lo que ya está en el disco
      no tiene sentido.

## H58 · Repaso de funcionalidades e interfaz — ADR-095 · docs/IDEAS.md
- [x] K1 **Indicador de trabajo en la barra**: icono con el número de tareas mientras hay algo en marcha, con el
      nombre y el porcentaje de la que corre; al pulsarlo, Tareas. Sin trabajo no está.
- [x] K2 **Avisar antes, no después**, escrito como regla en docs/INTERFAZ.md y aplicado en tramos, mandos del
      invitado e «ir a un minuto». Queda repasar el resto del menú con ese criterio.
- [x] K3 **Volumen parejo y temporizador, donde se buscan** — ADR-100. Las dos piezas ya existían y estaban
      escondidas. ReplayGain funcionaba —etiquetas por pista o por álbum, y para música sin etiquetas la ganancia
      que mide mpvd— pero solo se ofrecía en *Música*, y viendo una película nadie entra ahí; ahora la fila está
      también en *Imagen y sonido*, **pegada** a la del nivelador y diciendo lo que cuesta cada vía: las etiquetas
      son gratis pero solo donde las hay, el filtro vale para todo pero cuesta CPU. El ajuste sigue siendo uno
      solo, el de `mu-music`, que es quien lo aplica. El temporizador ya valía para cualquier reproducción, no solo
      para audiolibros: ahora tiene puerta propia en el menú principal y en la paleta.
- [x] K4 **«La charla de una hora en quince minutos»**: `semantic.highlights` elige los tramos que mejor la
      representan (cortados por frases enteras) y el reproductor los monta con `edl://`, sin recodificar nada y al
      instante. Guardar el archivo es un paso aparte, porque eso sí cuesta.
- [x] K5 **Ir a un minuto escribiéndolo** (icono y `g`): `2:15`, `1:02:15`, segundos sueltos y `+30` / `−30`.

## H60 · Quitar el vídeo al minimizar — ADR-096 · [x]
- [x] G1 Medido: minimizar **no** deja de decodificar (37 % → 18 % de un núcleo; 8 % sin vídeo, mismos fotogramas
      descartados), y deseleccionar la pista de un vídeo de internet **sí** corta su descarga (35 % de los datos)
      en 0,03 s, sin recargar y sin `ytdl_hook-all_formats`, que se descarta.
- [x] G2 `audio_minimized` encendido por defecto; vale para ficheros y para URLs.
- [x] G3 `alt+a` usa el camino instantáneo también en internet: ya no recarga ni cuenta otra reproducción.
- [x] G4 Fuera `prefer_audio` (los vídeos se abren siempre con imagen) y fuera el registro del botón `mu-audio`,
      que no se dibuja desde H51. El ahorro máximo de datos sigue en «Solo audio» del menú de calidad.

## H53 · El «modo sencillo» no encoge la barra (encontrado el 2026-10-02) — ADR-097 · [x]
- [x] F1 Arreglado por la vía (a), pero **una sola vez** y no script a script: el que esconde los botones es el
      módulo compartido `mu.uosc`, que es por donde pasan todos los `set-button` de los veinte scripts. Así lo
      heredan también los que aparecen más tarde (Tareas, grabación) sin tocarlos. Se conserva `mu-menu`, que es la
      puerta a todo lo demás, y se sigue escribiendo la opción `controls` para el próximo arranque. El test ya mira
      la barra: `mu.uosc` publica en `user-data/mu/bar/<script>` qué tiene escondido, porque uosc no publica sus
      botones. Lo que no se puede esconder son los elementos propios de uosc (play, anterior/siguiente, audio,
      velocidad, pantalla completa), que son justo los que el modo sencillo quiere conservar.
- [ ] F0 (histórico) `mu-modes` escribe `uosc-controls` al entrar en modo sencillo, pero **uosc solo lee esa opción al
      arrancar** (`Controls:init_options()` solo corre en `init()`, y ningún elemento escucha cambios de opciones).
      Comprobado con dos capturas: tras activarlo, el OSD dice «menú corto y barra mínima» y la barra se queda
      **idéntica**. El menú sí encoge; la barra no. El test actual solo comprueba que la opción se escribe, no que
      la barra cambie, por eso no saltaba.
      Dos arreglos posibles: (a) que cada script esconda su botón (`hide`, que la API pública de uosc sí admite)
      cuando mu-modes lo pida —toca mu-intro, mu-cut, mu-notes, mu-record—, o (b) un parche documentado en
      `patches/` para que uosc rehaga la barra al cambiar la opción. (a) no toca uosc y es lo que recomiendo.
      Al arreglarlo, el test tiene que mirar la barra, no la opción.

## H45 · El resumen en los vídeos de internet (§B.2.1-2) — ADR-081
- [x] D1 Para una URL, pedir los subtítulos de la web antes de transcribir (5,41 s frente a ~8 min de whisper), al
      pulsar y no al abrir el vídeo, con el aviso de «Buscando los subtítulos del vídeo…» y caché para la siguiente.
      NO se añade `write-auto-subs` a mpv.conf: con `--sub-langs all` el `-J` pasa a 14 MB en cada vídeo (ADR-056).
- [x] D2 Solo los idiomas nativos: lo garantiza `subs.web.list`, que nunca ofrece las traducciones automáticas de
      YouTube (HTTP 429). Si la pista no está en castellano, una fila lleva a traducirla con OPUS-MT.
- [x] D3 «Resumen e índice» (las dos cosas en una entrada) en la raíz del menú —como fila del archivo que se está
      viendo, así que la raíz sigue con ocho categorías—, en el panel de subtítulos y en Subtítulos. Sale de
      «Herramientas», donde estaba en el tercer nivel.

## H46 · Un solo menú (§B.1, §B.3) — ADR-083
- [x] E1 Retirado el árbol de comentarios de menú de `input.conf`, que ahora tiene 78 teclas y nada más; fuera la
      tecla que abría el menú nativo de uosc. Dos cosas vivían SOLO allí (*Repetir la lista* y *Orden aleatorio*) y
      se trajeron antes de borrar. Y la paleta leía de ahí los títulos: los 67 que faltaban pasan a `CURATED`, con
      la tecla leída del reproductor para que no se quede vieja.
- [x] E2 Las cuatro duplicaciones estaban en ese árbol y se van con él. Un test impide que ninguna vista de
      `mu-menu` repita un título.
- [x] E3 Raíz de ocho categorías (lo comprueba el mismo test). Lo que solo sirve para el archivo que se está viendo
      («Continuar viendo», «Resumen e índice») va como fila contextual, no como categoría fija.
