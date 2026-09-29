# Decisiones (ADR)
- ADR-001 · mpvd en Python 3.12 + asyncio (MVP): iteración rápida con Claude Code y ecosistema IA (faster-whisper, Argos, ONNX, SDK MCP).
  El contrato JSON-RPC versionado permite reescribirlo en Rust/Go más adelante. Empaquetado futuro con PyInstaller/Nuitka.
- ADR-002 · Configuración portable: mpv siempre con --config-dir del proyecto; no se toca ~/.config/mpv.
- ADR-003 · Local-first: nube solo opcional y desactivada por defecto.
- ADR-004 · uosc solo mediante su API pública; nada de fork.
- ADR-005 · Vendorizado de uosc: las fuentes Lua y las fuentes tipográficas van en git (mpv-config/scripts/uosc, mpv-config/fonts);
  los binarios ziggy (18 MB; solo portapapeles y OpenSubtitles) quedan fuera de git y los restaura tools/vendor.sh verificando
  el SHA-256 de vendor.lock. Actualizar uosc = editar vendor.lock → tools/vendor.sh → commit.
- ADR-006 · Python 3.12 fijado con uv (.python-version) aunque el sistema traiga 3.14: el ecosistema IA (onnxruntime, ctranslate2,
  faster-whisper) publica ruedas para 3.12 antes que para 3.14.
- ADR-007 · Un socket IPC por instancia: bin/mpv-uos usa $XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock (fallback /tmp); mpvd vivirá en la
  misma carpeta (mpvd.sock). En Windows será \\.\pipe\mpv-uos-<pid> (pendiente).
- ADR-008 · Tests de red: pytest excluye el marcador `network` por defecto; tools/check.sh los ejecuta además si hay conectividad
  (HEAD a github.com) salvo MU_SKIP_NETWORK=1. Ambos bloques deben pasar para dar verde.
- ADR-009 · Medios de prueba generados y no versionados (tests/fixtures/media) por tools/make_test_media.sh (ffmpeg + espeak-ng);
  check.sh y el fixture de pytest los generan si faltan. Un manifest.json describe duraciones, frases y palabras clave esperadas.
- ADR-010 · Transporte script↔mpvd: el Lua de mpv no tiene sockets, así que mu-core solo lanza un subproceso corto
  (`python -m mpvd ensure --attach <ipc>`) que arranca el daemon si hace falta y le pide que se conecte al IPC de ESTE mpv. Desde ahí
  mpvd es cliente JSON IPC de mpv: recibe peticiones como `script-message mu-rpc <json-rpc> [script]` (evento client-message), responde
  con `script-message-to <script> mu-reply <json-rpc>`, observa propiedades y detecta el cierre por EOF. Sin procesos por llamada.
- ADR-011 · Identidad de archivo: clave de caché `mu:<blake2b-128(tamaño + 64 KiB inicio + 64 KiB fin)>`; se calcula también el hash
  OpenSubtitles (misma lectura) para búsquedas de subtítulos. En archivos < 128 KiB las palabras parciales se ignoran (comportamiento
  no definido por OpenSubtitles). Las URLs usan `url:<blake2b(url normalizada)>`.
- ADR-012 · El daemon se apaga solo tras 10 min sin sesiones ni clientes (MPVD_IDLE_TIMEOUT); cada instancia de mpv lo relanza al vuelo.
  Runtime dir compartido con bin/mpv-uos; un lock de archivo evita arranques dobles cuando abren dos mpv a la vez.
- ADR-013 · Menús de uosc en modo callback con estado propio: cada script mu-* lleva su pila de vistas y publica `user-data/mu/<x>`
  (view, depth, current, last_error…) para tests y diagnóstico. El evento `close` de uosc NO se usa para el estado: uosc lo envía desde
  su propio hilo mientras reemplaza un menú (el viejo ya destruido, el nuevo aún no creado) y tras la animación de cierre. En su lugar
  se observa `user-data/uosc/menu/type` en modo nativo (¡`mp.get_property` devuelve el JSON con comillas para las sub-claves de
  user-data!) y la navegación se reinicia solo si 200 ms después sigue sin haber un menú nuestro abierto. `show()` actualiza el menú
  abierto (`update-menu`) y solo abre uno nuevo al cambiar de tipo o al salir de la paleta. Las listas grandes viajan compactas
  (`compact=true`) y las líneas JSON del IPC/JSON-RPC admiten hasta 32 MiB (una lista completa de iptv-org son varios MB).
- ADR-014 · Runner nocturno tolerante al cupo: si la iteración termina con el aviso de límite de sesión de Claude, se espera hasta la
  hora de reset que indica el mensaje (o 30 min) sin contarlo como fallo; cada iteración tiene tope de 3 h; effort `high` por defecto
  para rendir más turnos por ventana de cupo. Se lanza de noche con `DEADLINE=07:30`.
- ADR-015 · yt-dlp vendorizado como asset `yt-dlp` (zipimport, 3 MB) fijado en vendor.lock e instalado en vendor/bin por tools/vendor.sh;
  mpvd lo ejecuta siempre con el Python del .venv y ytdl_hook con su shebang. Actualización: mpvd comprueba `releases/latest` como
  máximo una vez al día (caché HTTP 24 h) y, si `auto_update` (por defecto sí; `ytdl.settings.set {"auto_update":false}` o
  `MPV_UOS_YTDLP_AUTO_UPDATE=0`), descarga el asset, lo verifica contra el `SHA2-256SUMS` oficial y lo reemplaza atómicamente.
  Nunca se usa `-U` (reescribiría el fichero sin verificación) y siempre se pasa `--no-update --no-remote-components`.
- ADR-016 · Runtime JS para YouTube: se usa deno si existe (vendor/bin/deno o PATH) y, si no, node ≥ 22 de PATH, vía
  `--js-runtimes <nombre>:<ruta>` (mpvd) y `ytdl-raw-options js-runtimes=…` (ytdl_hook, aplicado por mu-ytdl al conectar).
  deno (150 MB) solo se vendoriza si no hay ningún runtime (o `MU_VENDOR_DENO=1`); sus sumas van en vendor.lock. Hoy YouTube funciona
  sin runtime (cliente visionos) pero está deprecado (docs/YTDLP.md §4).
- ADR-017 · ytdl_hook se configura en caliente desde mu-ytdl con `change-list script-opts append ytdl_hook-ytdl_path=<vendor>:yt-dlp`
  (ytdl_hook observa `options/script-opts` y repite la búsqueda del ejecutable; `script-opts/<clave>` no existe como propiedad;
  la lista usa `:` en Unix y `;` en Windows). Así la config sigue siendo portable (sin rutas absolutas en mpv-config).
  Verificado contra el ytdl_hook embebido en mpv 0.41 (docs/MPV_YTDL.md).
- ADR-018 · Cambio de calidad y modo solo audio = recarga con opciones por archivo:
  `loadfile <url> replace -1 {ytdl-format=…, vid=no|auto, start=<time-pos>}` (índice -1 obligatorio desde mpv 0.38). pause, speed
  y volumen son globales y se conservan. Para el menú "Calidad" mu-ytdl reutiliza el JSON que ytdl_hook ya obtuvo
  (`user-data/mpv/ytdl/json-subprocess-result.stdout`) como semilla de la caché de `ytdl.info` (clave `url:<hash>`, artefacto
  `ytdl-info`, versión = versión de yt-dlp, TTL 6 h porque las URLs de googlevideo caducan). `all_formats=yes` se descarta (lento).
- ADR-019 · Contenedor de descarga: para merges se pasa una lista de preferencia (`--merge-output-format mp4/mkv`, yt-dlp elige el
  primero compatible con los códecs); el remux de archivos únicos solo entre pares seguros (`mov/m4v/flv/3gp>mp4`, `mkv` acepta
  todo, webm no remuxa) porque `--remux-video mp4` falla con Theora/Vorbis (comprobado con archive.org). Nunca se recodifica vídeo.
  Selectores siempre con `*`/`+` (`bv*[height<=?H]+ba/b[height<=?H]/bv*+ba/b`): YouTube ya no ofrece formatos combinados.
- ADR-020 · Las descargas no pertenecen a la sesión de mpv que las pidió: siguen aunque se cierre el reproductor (mpvd no se apaga
  mientras haya trabajos) y su progreso se difunde a todas las instancias conectadas como `script-message-to mu_ytdl mu-event`
  (≤4 Hz por descarga). Historial (últimas 200) y ajustes en data_dir (`downloads.json`, `ytdl.json`); carpetas por defecto
  `<XDG Vídeos|Música>/MPV-UOS` (user-dirs.dirs, fallback ~/Videos, ~/Music, ~/Downloads).
- ADR-021 · "Continuar viendo" por contenido, no por ruta: mpvd guarda en data_dir/watch.sqlite3 la posición por clave `mu:<hash>`
  (archivos) o `url:<hash>` (URLs) mediante `watch.*`; mu-menu consulta `watch.get` en file-loaded ANTES de registrar la reproducción
  y hace `seek` si mpv no reanudó ya (time-pos ≤ 5 s) y la posición supera 20 s; guarda cada 15 s, al pausar, al buscar y al terminar
  (eof ⇒ terminado). "Terminado" = últimos 30 s (o 10 % en medios cortos) o ≥95 %. Los directos de TV/radio no se registran
  (mu-iptv publica `current_url`). Convive con `save-position-on-quit` de mpv (por ruta), que tiene prioridad si actúa.
- ADR-022 · Menú raíz propio y paleta: MBTN_RIGHT/MENU/alt+m abren el menú "MPV-UOS" de mu-menu (dinámico: continuar viendo inline,
  entradas según haya archivo) y `ctrl+m` conserva el menú completo que uosc genera de input.conf. La paleta (alt+p) agrupa comandos
  (bindings con comentario `#!` leídos de `input-bindings` + lista curada en español), canales (`iptv.search`), recientes
  (`watch.search`) y acciones de mpvd; el filtrado de comandos es local (sin acentos) y el resto lo resuelve mpvd. La pantalla de
  inicio es el mismo menú en vista "start", abierto solo si mpv arranca en idle sin lista (opción `mu-menu-start_screen`; los tests
  la desactivan con `--script-opts-append`, ya que un `--script-opts=` posterior sustituye la lista entera).
- ADR-023 · whisper.cpp vendorizado por copia, no por compilación: tools/vendor_whisper.sh copia whisper-cli/whisper-server, las
  libs ggml/whisper y los modelos ya presentes desde ~/proyectos/live-captions-linux/whisper.cpp/build (o $WHISPER_BUILD) a
  vendor/whisper/{bin,models}; se ejecutan con LD_LIBRARY_PATH (DYLD_LIBRARY_PATH en macOS). Compilar v1.9.4 en vendor/whisper-src
  tardó más de 45 min en este portátil y se descartó. Los subtítulos IA cubren de momento solo archivos locales con duración
  conocida: para URLs y directos hará falta grabar el audio con ffmpeg desde la URL resuelta (pendiente, H5 ampliado).
- ADR-024 · Un proceso `whisper-cli` por trozo en vez de `whisper-server` persistente: medido en esta máquina, el coste fijo de cada
  llamada es el encoder sobre la ventana de 30 s (tiny 1,3 s · base 3,0 s · small-q5_1 12 s con 3 hilos, incluso con 1 s de audio) y no
  la carga del modelo, así que un servidor no ahorraría nada relevante y añadiría un puerto y un proceso que vigilar. Consecuencias:
  trozos de 20 s por defecto (MPV_UOS_ASR_CHUNK), un solo proceso whisper a la vez (cerrojo global), hilos = núcleos-1 (máx. 8) y
  modelo por tier de hardware según docs/BENCHMARKS.md: en vivo se exige RTF ≤ 0,5 dejando CPU libre para mpv.
- ADR-025 · Traducción offline con paquetes Argos Translate ejecutados directamente sobre CTranslate2 + sentencepiece (extra opcional
  `translate` del pyproject, ≈190 MB en el .venv), sin `argostranslate` (arrastra stanza → torch + CUDA, varios GB). Paquetes 1.0
  es↔en fijados por SHA-256 en vendor.lock (el es→en 1.9 usa BPE y devuelve basura en int8: issue #504); el resto del índice oficial se
  descarga bajo demanda y los pares sin modelo directo pivotan por inglés. Los cues se agrupan en frases (≤3 cues, hasta puntuación
  final) antes de traducir y la traducción se reparte por longitud, así los tiempos no cambian. Caché por (hash, ruta de modelos,
  idiomas, beam, hash del SRT).
- ADR-026 · Resincronización sin DTW completo: emparejamiento por solapamiento de palabras (Jaccard-coseno sin acentos) dentro de
  una banda de ±120 s y cadena monótona de máximo peso (LIS ponderada con árbol de Fenwick, O(n log n)); el desfase es una recta
  robusta (Theil–Sen) por ventana (300 s o corte cuando salta >1,5 s) con extrapolación en los extremos, lo que corrige retraso
  constante, deriva por fps y cortes de publicidad. Referencia: segmentos de Whisper de la tarea `asr` del archivo (se lanza en
  baja prioridad si no existe).
- ADR-027 · Sonido e imagen con lavfi etiquetado dentro de mpv (no en mpvd): cada preset es un grafo `@mu-<x>:lavfi=[...]` validado
  contra el mpv/FFmpeg instalados (docs/AUDIO_VIDEO.md); mpvd solo aporta los modelos (RNNoise, HRTF SOFA) fijados por SHA-256.
  Con modelo ausente cada filtro degrada a una alternativa sin archivos (afftdn, crossfeed) y avisa en pantalla. El "perfil ligero"
  cambia opciones en caliente y las restaura; el diagnóstico de tirones solo lee contadores de mpv y sugiere, nunca cambia hwdec/vo
  por su cuenta.
- ADR-028 · MCP sin SDK: el paquete oficial `mcp` 2.2 arrastra ~30 dependencias (pydantic, starlette, uvicorn, cryptography,
  opentelemetry…) para un servidor que solo necesita stdio + JSON-RPC, así que `mpvd/mcp.py` implementa a mano el subconjunto de la
  especificación 2025-06-18 (initialize, tools, resources, prompts vacío, ping). Las tools son envoltorios de los métodos JSON-RPC
  de mpvd (`session.*`, `asr.search`, `iptv.*`, `ytdl.download`, `notes.*`); las acciones que interrumpen al espectador piden
  confirmación en pantalla (diálogo uosc de mu-menu con token, 15 s) salvo `--yes`/`MPVD_MCP_AUTOCONFIRM=1`. Si más adelante hace
  falta transporte HTTP/streamable o OAuth, se evaluará el SDK como extra opcional.
- ADR-029 · Intro/créditos por huellas de audio entre episodios, sin base de datos externa: `fpcalc -raw -json` (Chromaprint
  1.6, ~8 sub-huellas de 32 bits por segundo) sobre los primeros y últimos 5 min de cada archivo (audio extraído con ffmpeg a 16 kHz);
  candidatos de alineación por votos de coincidencia exacta en los 20 bits altos y, en cada diagonal, la racha más larga con distancia
  de Hamming ≤6 tolerando 2 fallos; se descartan rachas "planas" (silencio, tono fijo) y el consenso entre hasta 3 vecinos (por número
  de episodio) es la mediana. Los bordes se ajustan (±2 s) al fin/inicio de silencios (-45 dB, 0,4 s) y negros (0,3 s). Se descartó
  whisper-server/ASR para esto (caro) y también comparar vídeo (perceptual hash) porque el audio es mucho más barato y basta.
  Huellas y resultado en la caché SQLite por hash (reanalizar un episodio nuevo reutiliza las huellas de los vecinos) y además se
  exporta `<carpeta>/.mpv-uos/segments.json` estilo media-segments (Jellyfin/Plex) para otros reproductores. Solo archivos locales.
  Sin `fpcalc` en PATH (`libchromaprint-tools`) `capabilities.services.intro=false` y mu-intro no molesta.
- ADR-030 · Embeddings sin torch y vectores en NumPy: `onnxruntime` + `sentencepiece` (extra opcional `semantic`, ≈30 MB) con
  `Xenova/paraphrase-multilingual-MiniLM-L12-v2` cuantizado u8u8 (118 MB, 384 dims, mean pooling; 2× más rápido que la variante
  quint8_avx2 en CPUs sin VNNI) fijado por SHA-256 en vendor.lock. Se descartan `tokenizers` (arrastra huggingface-hub y 270 MB de
  RSS; sentencepiece produce ids idénticos con el mapeo fairseq) y `sqlite-vec` (funciona, pero una película son <2 000 frases:
  `emb @ q` en NumPy tarda <1 ms; los vectores van como blob float32 en la caché de artefactos por (hash, "embed", modelo, versión)).
  Capítulos por cambio de tema sin LLM: ventanas de 45 s solapadas 50 %, d = 1 − cos entre consecutivas, media móvil 3, máximos
  locales sobre el percentil 85, mínimo 180 s por capítulo, título = frase más cercana al centroide (una cita, nunca inventada);
  se aplican a `chapter-list` (escribible en mpv 0.41) guardando los originales. La búsqueda semántica cae a coincidencia literal
  cuando no hay índice/modelo y lanza el indexado en segundo plano; ambos requieren la transcripción del servicio `asr`.
  Ganchos de test: `MPVD_SEMANTIC_FAKE=1` (bolsa de palabras determinista) y `asr.inject` (transcripción sintética).
- ADR-031 · Modo estudio dentro de mpv con mpvd solo para lo pesado: repetir línea = `ab-loop-a/b` sobre `sub-start`/`sub-end`
  **más `sub-delay`** (verificado: esas propiedades no incluyen el retardo y son `unavailable` sin cue; `ab-loop-a` vale la string
  "no" cuando no hay bucle y sus notificaciones llegan en diferido, así que el observador relee el valor síncrono). Velocidad
  inteligente = `speed` en caliente (≤5 Hz) sobre un mapa de silencios de `silencedetect` (-30 dB, 0,5 s; -35/0,6 no detecta las
  pausas de una locución) calculado por mpvd en ventanas de 10 min y cacheado por hash; se descartó el VAD Silero de whisper.cpp
  por ahora (más preciso pero exige extraer audio y otro proceso; silencedetect basta y funciona sin modelos). Clips = ffmpeg en
  un job de mpvd (`-ss` antes de `-i`; el modo sin recodificar corta en keyframe y puede empezar antes, el mp4 exacto recodifica
  con libx264 veryfast crf 23; GIF con palettegen/paletteuse a 12 fps y 480 px; `stream-record` descartado: solo graba lo que entra
  en caché y falló con dts). Notas: se reutiliza `notes.add` de H8 (un Markdown por clave de contenido) y el cuadro de búsqueda de un
  menú de uosc en modo paleta hace de campo de texto.
- ADR-032 · Mando QR/PWA sin dependencias: HTTP/1.1 mínimo sobre `asyncio.start_server` (`mpvd/remote/http.py`: rutas, JSON,
  estáticos, SSE) en vez de aiohttp, y codificador QR propio en Python puro (`mpvd/remote/qr.py`: modo byte, versiones 1–10, nivel M,
  8 máscaras con penalización; `qrencode`/`segno` no están instalados y no merecen una dependencia). El QR se dibuja en mpv como
  overlay ASS vectorial (`mp.create_osd_overlay` + rectángulos por racha de módulos), no con `overlay-add` (exige fichero BGRA y
  coordenadas de pantalla). Emparejamiento: token de un solo uso (72 bits, 10 min) en el fragmento `#t=` de la URL, canjeado por una
  cookie `HttpOnly; SameSite=Strict` firmada con HMAC-SHA256; los móviles emparejados persisten en `<datos>/remote.json` (0600) hasta
  "Olvidar", que rota el secreto. API = lista blanca de órdenes (nada de comandos arbitrarios), POST con `Origin` = `Host`, estado por
  SSE a ≤2 Hz. HTTP sin TLS en la LAN (un certificado autofirmado rompe la PWA en móviles); el servidor solo arranca a petición del
  usuario (alt+z o menú) y recuerda `autostart` hasta que se detiene. Puerto 8790 (o uno libre si está ocupado).
- ADR-033 · Datos de usuario fuera del proyecto: favoritos, recientes, notas, móviles emparejados, preferencias y `watch_later`
  viven en `$XDG_DATA_HOME/mpv-uos` (`MPV_UOS_DATA_DIR`) también en desarrollo; `.cache/` del proyecto queda solo para caché
  desechable. bin/mpv-uos migra una vez (copia, sin borrar) `.cache/data` y `mpv-config/watch_later` (limpiando las claves globales)
  y solo si se usa la ruta por defecto; los tests fijan siempre su propio `MPV_UOS_DATA_DIR` (fixture de sesión en conftest).
- ADR-034 · El reproductor nunca se cierra solo: bin/mpv-uos añade `--idle=yes` salvo que el llamante elija `--idle`; sin archivo
  abre la ventana (pseudo-gui). mu-core recoge las líneas de error del log mientras se abre un archivo y, si la carga falla, explica
  la causa en español en pantalla (tabla de más específica a más genérica: DRM, inicio de sesión, geobloqueo, no disponible, 403/404,
  archivo inexistente, servidor caído…); mu-menu vuelve a la pantalla de inicio.
- ADR-035 · Una sesión de mpvd por proceso de mpv: el socket de cada instancia nunca se hereda del entorno (un gestor de archivos
  lanzado desde mpv lo pasaba al siguiente mpv-uos), bin/mpv-uos borra los sockets de procesos muertos y mpvd rechaza enlazar un
  socket que responde con otro PID (antes la segunda ventana compartía la sesión de la primera y sus llamadas caducaban).
- ADR-036 · watch_later solo guarda lo propio de cada archivo (`watch-later-options`: posición, pistas, retardos, encuadre) y
  `reset-on-next-file` impide que eso pase al siguiente; volumen, velocidad, filtros y estilo de subtítulos son preferencias
  globales (mu-prefs). Antes el archivo siguiente heredaba volumen, velocidad, filtros y un `aid` inexistente (sin sonido).
