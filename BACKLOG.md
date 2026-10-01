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

## H26 · Torrents · FUERA POR AHORA (decisión de Ser, 2026-09-30: no implementar)
- [~] Propuesta aparcada: integrarse con qBittorrent (ya instalado; Ser activa su interfaz web en localhost) para añadir magnets, descargar en
      orden y «ver mientras descarga» en MPV-UOS, con libtorrent en el .venv solo como alternativa si no hay qBittorrent.

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

## H41 · Nombre (lo decide Ser)
- [ ] Elegir entre Lince, Cauce, Lumbre, Mirador, Compás, Querencia (o Sintonía, que quedó descartado) y cambiarlo en
      brand.json, que es el único sitio donde vive.

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

## H43 · Grabar: formato, programación visible y radio (§B.2.4-6)
- [ ] B1 Fila «Formato» en el menú de grabar, que se recuerda: igual que el original (sin recodificar) / MP4 /
      solo audio en Opus 128. Hoy no se puede elegir: `KIND_HINT` solo informa.
- [ ] B2 «Programar una grabación» visible en el primer nivel de TV y radio y también en Grabar, no solo con Tab
      dentro de la lista de un canal.
- [ ] B3 La radio se puede programar: quitar `ch.kind ~= 'radio'` de mu-iptv:449 y :1042. mpvd ya graba audio bien.
- [ ] B4 Al volver a pulsar el botón de grabar, termina sin preguntar nada (ya hecho en H35; comprobar que sigue así
      con la fila de formato nueva).

## H44 · La sala: fichero original y el reproductor del invitado (§A)
- [ ] C1 Handler Range que lea por trozos en vez de `path.read_bytes()`: hoy una película de 4 GB se cargaría entera
      en RAM por petición. Prototipo medido: 2 MB de RSS sirviendo un fichero de 428 MB.
- [ ] C2 Ruta `GET /s/<sala>/file` que sirva el fichero original. Medido como invitado: primer fotograma en 0,36 s y
      salto al minuto 98 en 0,07 s, sin recodificar nada.
- [ ] C3 Bloque «Abrir en mi reproductor» en la página del invitado: copiar el enlace, descargar un `.m3u` (doble clic
      lo abre en VLC o mpv) y la línea `mpv "<enlace>"`. Con la posición del anfitrión en vivo y un botón para copiarla.
- [ ] C4 Decidir el camino según el origen, sin preguntar: fichero local con códecs de navegador → el original;
      con otros códecs → las dos cosas a la vez (tu reproductor, y remux `-c copy` a fMP4 o WebM para el navegador,
      9,64 s para 115 min); TV, radio, YouTube o grabación en curso → relay.
- [ ] C5 El relay empieza donde está el anfitrión, no en el segundo 0, y se lo dice al invitado. Es la causa de los
      ~18 min de espera al entrar en el minuto 40.
- [ ] C6 Unirse a la sala desde MPV-UOS (no solo desde el navegador): el mpv del invitado sigue pausa, saltos y
      velocidad por el SSE que ya existe. Es la única forma de «ver juntos» exacto a calidad original.
- [ ] C7 ADR: WebTorrent como no-objetivo, con los números (subida medida de 167 Mb/s = 15 a 60 invitados; no resuelve
      los códecs del navegador, ni la sincronía, ni el directo, y añade tracker, WebRTC y a veces TURN).

## H45 · El resumen en los vídeos de internet (§B.2.1-2)
- [ ] D1 Para una URL, pedir los subtítulos automáticos antes de transcribir: medido en 5,41 s para un vídeo de 15 min
      (23 KB, 3.678 palabras) frente a los ~8 min de whisper.
- [ ] D2 Pedir solo los idiomas nativos del vídeo: pedir una traducción automática de YouTube devuelve HTTP 429.
      El castellano se consigue traduciendo el SRT con OPUS-MT, que además queda mejor.
- [ ] D3 «Resumen e índice» en la raíz del menú y como fila dentro del panel de subtítulos. Hoy «¿Qué me he perdido?»
      está en el tercer nivel y «Índice del vídeo» no está en el menú principal.

## H46 · Un solo menú (§B.1, §B.3)
- [ ] E1 Retirar el árbol `#!` de input.conf (119 entradas, 40 en el primer nivel) y dejar `input.conf` solo para las
      teclas. mu-menu pasa a ser el único menú.
- [ ] E2 Quitar las cuatro duplicaciones (Biblioteca, Música, Audiolibros, Saltar intro aparecen dos veces).
- [ ] E3 Raíz de ocho filas o menos, nombradas por lo que quieres hacer. Lo que solo sirve para el archivo que estás
      viendo va a la pantalla o al panel de su función, no a la raíz.
