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
  `--js-runtimes <nombre>:<ruta>` (mpvd) y `ytdl-raw-options js-runtimes=…` (ytdl_hook, mu-ytdl pone `js-runtimes=node` al cargar (antes del primer archivo) y lo sustituye por el valor de mpvd al conectar; un valor del usuario no se toca).
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
  modelo por tier de hardware según docs/BENCHMARKS.md. (La parte de «en vivo se exige RTF ≤ 0,5» quedó sin efecto:
  ADR-070 quitó la transcripción en vivo y ahora el modelo se elige por calidad.)
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
- ADR-037 · Preferencias del usuario en `<datos>/prefs.json` (mu-prefs + `script-modules/mu/prefs.lua`), no en watch_later (es
  por archivo) ni en `~~state/`/`~~cache/` (son del mpv personal). Un espacio de nombres por script; precedencia: CLI
  (`option-info/<x>/set-from-commandline`, `--script-opts`) > prefs.json > script-opts/*.conf > código. Todos leen al arrancar y
  aplican en su bloque principal (antes del primer archivo); escribe solo mu-prefs (los demás envían `mu-prefs-put`: cada script
  es un hilo y varios volcando a la vez al salir perdían cambios), con debounce de 1,5 s y escritura atómica que mezcla solo las
  claves cambiadas. Solo cuentan las acciones del usuario (menú, tecla, mensaje); mu-prefs ignora el valor inicial, lo que pasa
  al abrir un archivo, lo `set-locally` y la velocidad de los silencios de mu-study. No se guardan pausa, posición, ids de pista,
  retardos, encuadre ni desentrelazado (son del archivo), ni repetir archivo (repetiría todo) ni el aleatorio de uosc (sin API).
  Elegir una pista con idioma lo pone al frente de alang/slang; apagar subtítulos se recuerda. «Solo audio» de mu-ytdl se aplica
  en un hook on_load previo al de ytdl_hook. Restablecer mueve el fichero a `prefs.json.bak-<ts>` y avisa a todos los scripts.
- ADR-038 · Traducción con dos motores y SRT guardables: OPUS-MT tc-big (Helsinki-NLP, CC-BY 4.0) para es/ca↔en (francés
  añadido en H36/C6: es/ca/fr ↔ en, y el motor por defecto pasa a ser OPUS-MT donde llegue), descargado bajo
  demanda (zips oficiales de 863 MB fijados por SHA-256) y convertido una vez a CTranslate2 int8 (234 MB, OpusMTConverter: solo
  numpy+pyyaml) en <datos>/models/opus-mt; beam 4 en segundo plano y fuera de memoria tras cada trabajo. Argos queda para los demás
  pares y el pivote; engine=auto usa OPUS-MT solo si ya está descargado. Descartados NLLB (CC-BY-NC) y LLM local (lento en CPU).
  Pre-subtitulado con small-q8_0 en el tier small, trozos de 28,5 s (una ventana de 30 s de whisper) y --prompt con el final del
  trozo anterior. subs.save escribe <vídeo>.<idioma>.srt atómico (.ia/.resync/(2); si no se puede, ~/Vídeos/MPV-UOS/Subtítulos);
  los subtítulos de imagen (PGS/VobSub/DVB) se rechazan: el OCR queda fuera de alcance.
- ADR-039 · Directos: sin watch_later (mu-iptv borra la entrada antes de cada loadfile y mpvd añade save-position-on-quit=no por
  archivo); en HLS `demuxer-lavf-o=http_persistent=0,seg_max_retry=3` fusionado sin pisar el de la lista (un servidor se
  congelaba a los 12 s reutilizando la conexión); User-Agent de navegador cuando la lista no da uno (Canal Sur daba 403 a
  `libmpv`). La calidad sale de la lista maestra en la comprobación de salud (ffprobe completa); canales repetidos (nombre
  normalizado + grupo) en una entrada con el oficial primero, las copias FAST con anuncios al final y cambio automático de fuente
  si no abre (mu-core calla su aviso mientras quedan fuentes). Etiquetas en español (iso-codes del sistema o JSON incluido); los
  valores crudos no cambian (filtros, favoritos, MCP). La resolución, los 25 fps y el bitrate son de la fuente.
- ADR-040 · Intro/créditos más allá de «una carpeta por temporada» (sustituye la exportación automática de ADR-029): vecinos en la
  misma carpeta y, si no hay de la serie, en carpetas hermanas por título normalizado + temporada; principio (600 s) y final (300 s)
  independientes y vecinos rotos o sin audio saltados; se descarta lo que no tiene sentido (intro tras el 40 %, créditos antes del
  60 %, solapes → gana el de más vecinos) y las versiones del mismo vídeo (>50 % de audio común o ≥5 fragmentos sin nombres de
  episodio); bordes afinados solo ±6 s (coste casi fijo). Marcas manuales = datos de usuario (`<datos>/intro-marks.json`)
  trasladadas a la temporada localizando su huella. Nada se escribe junto a los vídeos salvo `intro.export` (segments.json con
  Type/StartTicks de Jellyfin). Detección y salto automático se recuerdan (mu-prefs).
- ADR-041 · Un solo menú con migas (H15): cada script sigue siendo un hilo con su propio tipo de menú en uosc, pero todos usan
  `script-modules/mu/nav.lua`: título con migas («MPV-UOS › TV y radio › España», máx. 4 tramos), fila «Atrás» primera en cada
  vista (la selección de teclado empieza en la siguiente), `⌫`/botón atrás del ratón (evento `back` de uosc) y `←` en una raíz
  (llega como `key` `left`: uosc solo lo usa si hay submenú padre o búsqueda) vuelven un nivel; la fila llama a
  `script-binding uosc/menu-back` para que dentro de un submenú de uosc vuelva al padre. La pila compartida es un traspaso: quien
  abre un módulo le manda `mu-nav-open <entrada> {crumbs, script, view}` y el módulo, al vaciar su pila, devuelve el control con
  `mu-nav-return <view>`; abierto con su tecla, su padre es la raíz del menú principal. Las paletas (URL, YouTube, canal) se cierran
  con `⌫` vacío. Menú principal en ocho categorías; TV y Descargas abren su módulo directamente y el resto son vistas de mu-menu
  que llevan a los módulos. Barra reducida (reproducción, subtítulos, audio, velocidad, grabar, menú, pantalla completa; ⏭ solo
  dentro de un segmento), `menu_item_height` 36→42 (letra ≈ 20 px), `?` ayuda (sustituye a la página de teclas de stats) y
  «Pausar con un clic» como preferencia desactivada: mientras está activa, `MBTN_LEFT` va a mu-menu (uosc sigue recibiendo los
  clics sobre sus elementos porque sus secciones se activan encima) y el clic deja de arrastrar la ventana.
- ADR-042 · Subtítulos IA sincronizados con la voz (H16): whisper-cli con `-ojf` (tiempos por token) y sin `-np` (su log
  trae la tabla tiempo-VAD → tiempo original: con `--vad` los tokens no vuelven solos). Se mantiene el VAD de Silero
  (evita alucinaciones en silencio y da los bordes de voz) y los tramos se parten con una VAD de energía en pausas ≥ 0,25 s;
  cada hueco entre tramos se asigna al límite entre palabras más plausible y las palabras se encajan en su tramo, porque los
  tiempos de token se desvían 0,2–0,5 s en los cambios de frase. Sin modelo VAD: `--dtw <tamaño> -nfa` (+~20 % de tiempo)
  y la VAD de energía. Descartado usar solo DTW sin VAD por defecto: más lento, alucina en silencios y no mejora los bordes
  frente a Silero. Cortes y reglas de lectura en `mpvd/asr/timing.py` (docs/WHISPER.md). La caché de transcripciones
  pasa a la versión 2: lo transcrito antes se rehace al volver a abrirlo (los SRT guardados no cambian).
- ADR-043 · «Mis notas» (H17): un Markdown por vídeo en `<datos>/notas` con el título como nombre (caracteres prohibidos en
  Windows/macOS/Linux fuera, «(2)» si se repite) y una cabecera YAML mínima (`titulo`, `video`, `clave`) que lo ata al
  contenido (misma clave que «continuar viendo»: mover o renombrar el vídeo no pierde las notas; la siguiente nota
  actualiza la ruta de todos los enlaces). Notas ordenadas por tiempo; editar y borrar por índice; el archivo se va con su
  última nota. Exportar = copia (junto al vídeo como `<vídeo>.notas.md` o a una carpeta recordada, p. ej. una bóveda de
  Obsidian); no se sincroniza después. Enlaces `mpv-uos://open?path=<ruta codificada>&t=<s>`: el `.desktop` declara
  `x-scheme-handler/mpv-uos` y el instalador lo registra siempre con `xdg-mime` (esquema propio, sin sudo; `--uninstall`
  lo quita); bin/mpv-uos los convierte en grupos por archivo `--{ --start=T ruta --}` antes del primer `--` (el `.desktop`
  pasa las URL tras `--`) y descarta un `t` que no sea numérico; dentro de mpv (pegar, lista) un hook `on_load` de
  mu-notes (prioridad 5, antes de ytdl_hook) hace `loadfile … replace -1 start=T`. Menú en un script propio (`mu-notes`,
  pila de vistas y migas de ADR-041); las notas se siguen tomando con `alt+b` en mu-study. Los archivos de la primera
  versión (`<clave>.md`, enlaces `mpv://seek`) se migran al leer la carpeta.
- ADR-044 · Botón «Grabar» unificado (H18): script `mu-record` (botón ● de uosc, `alt+r`, *Grabar* del menú principal).
  Según la fuente, fijada al empezar: **directo** (canal de mu-iptv; URL sin duración o que no permite buscar; por yt-dlp
  solo si su JSON dice `is_live`) → `stream-record` de mpv a `.mkv` (`.mka` para radio); solo audio → al parar mpvd
  copia la pista de audio (`record.audio`, contenedor según el códec) y borra el vídeo; tramo ya en caché → `dump-cache`.
  **Vídeo de internet** → yt-dlp `--download-sections "*A-B"` sin `--force-keyframes-at-cuts` (sin recodificar) y con
  formato H.264/AAC (`bv*[vcodec^=avc1]+ba[ext=m4a]`/`ba[ext=m4a]`): con webm/opus de YouTube el tramo sale mal (16 s en
  vez de 6, verificado); nombre con el tramo para que `--no-overwrites` no se salte otro tramo del mismo vídeo.
  **Archivo local** → `study.clip`: `mp4-copy` si los códecs caben en mp4 (la lista de edición hace que empiece justo en
  A) o `mkv-copy` (empieza en el fotograma clave anterior); `-avoid_negative_ts make_zero` quitado de ambos (en mp4
  borraba la lista de edición); solo audio → `audio-copy` (búsqueda gruesa en la entrada + exacta en la salida, FLAC
  reescrito en FLAC). Descartado `dump-cache` para locales (necesita `--cache=yes` y con fuentes MKV pierde los
  fotogramas B). Indicador: superposición ASS «● REC m:ss» a 1 Hz y contador en el botón; alt+r de mu-iptv queda como
  binding sin tecla. Carpeta recordada en mu-prefs; por defecto `<Vídeos>/MPV-UOS/Grabaciones` (`record.defaults`).
- ADR-045 · Gestor de descargas (H19): lote de URLs en mpvd (`ytdl.download.batch`: texto con cualquier separador,
  lista o `.txt` con comentarios `#`; sin repetidos; máx. 500), una descarga por URL (progreso y cancelación
  individuales). Listas y canales: una sola ejecución de yt-dlp con `--yes-playlist --playlist-items 1,3,…` (índices de
  la lista plana) y plantilla `%(playlist_title,playlist_id|Lista)s/%(playlist_index)03d - …`. Archivo de descargas
  (`--download-archive <datos>/ytdl-archive.txt`) solo en listas, canales y lotes: en un vídeo suelto impediría bajarlo
  otra vez en otro formato. Límite de velocidad global (`-r`, validado `N[KMG]`) y simultáneas (ya existía, 1–4). La cola
  sobrevive a reinicios: lo que no terminó se reencola al arrancar mpvd (`--continue` aprovecha los `.part`) en vez de
  marcarse «interrumpida».
- ADR-046 · Subtítulos al descargar (H19): tres modos en DownloadSpec (`subs_mode`): `embed` (como antes), `file`
  (`--convert-subs srt`, quedan junto al vídeo) y `only` (`--skip-download`, sin `-f`/`-S`). Idiomas por defecto
  `orig,es.*,en.*`; mpvd resuelve `orig` con el `language` del `-J` del vídeo (+ `.*-orig`, los automáticos del idioma
  original de YouTube); sin información se queda con el resto. Con `--skip-download` yt-dlp no llega a `after_move`
  (verificado con un vídeo real): las rutas de los `.srt` salen de `--print "after_video:MU_SUBS
  %(requested_subtitles.:.filepath)j"`, solo en `file`/`only` (en `embed` se borran tras incrustarlos). «Todos» =
  `all,-live_chat` (el chat de un directo no es un subtítulo).
- ADR-047 · yt-dlp nightly, suplantación y sesión del navegador (H19): el nightly (`yt-dlp/yt-dlp-nightly-builds`,
  mismos `yt-dlp` y `SHA2-256SUMS`) vive en `vendor/bin/yt-dlp-nightly`, se baja verificado bajo demanda y se refresca
  con la comprobación diaria; la estable sigue por defecto. Una descarga fallida se repite UNA vez con el nightly si el
  error no es de disponibilidad (privado, inicio de sesión, cookies, país, borrado, 404, formato inexistente, disco); al
  reproducir, mu-ytdl pone el nightly primero en `ytdl_hook-ytdl_path` para esa URL y luego lo quita (una vez por
  URL; el resultado de ytdl_hook se guarda al cargar porque `on_after_end_file` lo borra antes de `end-file`).
  Verificado el 2026-09-30: ok.ru falla con la estable («the JSON object must be str…») y abre con el nightly.
  `curl_cffi` como extra opcional `impersonate` (`>=0.10,<0.17`, el rango que acepta el yt-dlp vendorizado; 38 MB): sin
  él TikTok funciona hoy con un aviso. «Usar mi sesión del navegador» = `--cookies-from-browser` (lista y sintaxis del
  `--help`), desactivado, validado contra la lista (argv sin shell) y aplicado también a ytdl_hook; nunca DRM.
  TikTok: vídeo y perfil (lista plana con casillas) sin cookies; Instagram: reels y publicaciones sí, perfiles no
  (extractor marcado como roto en yt-dlp).
- ADR-048 · Convertir (H20): módulo `mpvd/convert` (presets → argv exacto de ffmpeg 8) y script `mu-convert`. Presets:
  «MP4 compatible» (libx264 CRF 20/23/28 + AAC, faststart), «Más pequeño» (libx265 CRF 24/28/32; mp4 con AAC y
  `-tag:v hvc1`, que exigen QuickTime/iOS, o mkv con Opus), «Web» = WebM VP9 (CRF 31/35/40, `-b:v 0 -row-mt 1`,
  `-deadline good -cpu-used 4`) + Opus (el formato abierto de la web; para compatibilidad total ya está MP4), solo audio
  (MP3 con ID3v2.3, M4A/AAC, Opus, FLAC, WAV; bitrate por calidad o elegido) y GIF en dos pasadas
  (palettegen → paletteuse, 10/12/15 fps, 320/480/640 px, máx. 60 s). Resolución máxima sobre el lado corto (un vídeo
  vertical no se tumba), tamaños pares, nunca se amplía. Tramo con `-ss/-t` de entrada; con subtítulos, además `-t` de
  salida (la de entrada deja pasar rótulos posteriores, verificado). Subtítulos de texto → mov_text (mp4) / WebVTT
  (webm); mkv los copia; los de imagen (PGS/VobSub) solo caben en mkv: se avisa. VA-API solo para H.264/H.265 cuando
  `vainfo --display drm` lo ofrece y ffmpeg tiene el codificador; con solo `VAEntrypointEncSliceLP` se pasa
  `-low_power 1` (Intel iHD de este portátil: H.264 sí, HEVC no); si la GPU falla, se repite por CPU. Una conversión
  cada vez, trabajo «pesado» (se pausa si la reproducción pierde fotogramas) y `nice 10`. Se escribe `nombre.part.ext`
  y se renombra al acabar (`-n`, « (2)» si existe). Carpeta entera: una tarea por archivo en `<salida>/<carpeta>`.
  Salida por defecto `<Vídeos>/MPV-UOS/Convertidos`. Lo pendiente al parar mpvd vuelve a la cola al arrancar.
  «Tareas» (`tasks.list` + eventos `task` a mu_convert) une descargas y conversiones.
- ADR-049 · Guía de TV y grabación programada (H21): guía de la `url-tvg` de cada lista (TDTChannels:
  `epg/TV.xml.gz`, XMLTV en UTC, 184 canales, ~4 días) descargada con la caché HTTP (ETag, 12 h) en un trabajo de
  prioridad baja y leída por partes (`iterparse`) a SQLite en la caché (`epg.sqlite3`); sin zona horaria se toma UTC y
  sin `stop` el programa acaba donde empieza el siguiente. Casado por `tvg-id` y, si falta, por nombre sin acentos,
  espacios ni signos. mu-iptv pide «ahora» por lote y lo guarda hasta que acaba el programa. Grabaciones programadas
  en mpvd (`<datos>/iptv-schedule.json`): ffmpeg `-c copy` con las cabeceras del canal, pistas por defecto de ffmpeg a
  `.mkv`/`.mka` en `<Vídeos>/MPV-UOS/Grabaciones`; se para por reloj enviando `q` (un HLS en directo empieza unos
  segmentos atrás y con `-t` terminaba antes); si el directo se corta, sigue en otra parte. Margen en «Grabar este
  programa»: 1 min antes y 3 después. mpvd no se cierra por inactividad mientras haya grabaciones pendientes o en
  curso. Equipo apagado o suspendido: sin systemd, cron, rtcwake ni sudo nada lo despierta ni arranca mpvd al iniciar
  sesión; lo perdido queda «perdida» al volver a abrir MPV-UOS; si vuelve dentro de la franja, empieza tarde. Aviso de
  escritorio al terminar (nunca con `MPV_UOS_NO_NOTIFY`) y en pantalla en todos los reproductores abiertos.
  Descartado programarlo en mpv (`stream-record`): exige tener ese canal abierto.
- ADR-050 · Biblioteca sin servidor y subtítulos de internet (H22). **Índice**: `<datos>/library.sqlite3` (carpetas,
  archivos, grupos película/serie); escaneo INDEX «pesado» (espera si el guardián ve tirones), incremental por tamaño y
  mtime (hash solo de lo nuevo), carpetas anidadas sin duplicados; se relanza si el último tiene más de 30 min al pedir
  la lista o las filas de inicio. Identidad = clave `mu:` de «continuar viendo» (progreso y visto sin tabla propia;
  mover o renombrar conserva el progreso). **Nombres**: parser propio (sin guessit): S01E02, 1x06, Temporada/Season N
  Capítulo M, Cap.102, «Serie - 05», número inicial en carpeta de temporada, carpeta por episodio; películas: el último
  año plausible que no sea la primera palabra (1917, 2001…, Blade Runner 2049). **Inicio**: «Seguir viendo» = archivos
  con posición reanudable; «Siguiente episodio» = el primero no visto tras el último terminado; una fila por título.
  **Siguiente episodio automático**: solo con archivos de la biblioteca y si la lista no sigue; `eof-reached` (keep-open)
  o end-file eof; cuenta atrás de 5 s (Esc cancela, Enter ya); no se duplica con mu-intro. **Carátulas**: local > TMDB
  > fotograma ffmpeg (10 %, máx. 5 min, 342 px, en caché); uosc no pinta imágenes en menús: se exponen en `library.*`.
  **TMDB** opcional y desactivado (clave v3 o token Bearer, sin caché HTTP para no escribir la clave en disco).
  **OpenSubtitles REST v1** (desactivado): `Api-Key`, `User-Agent: MPV-UOS v<versión>`, parámetros ordenados en
  minúsculas; hash verificado contra la referencia oficial y solo con archivos ≥ 128 KiB; primero por hash, luego por
  nombre; orden exacto > idioma preferido > sin traducción automática > sin SDH > de confianza > más descargas;
  anónimo o con cuenta; caché `<caché>/library/subs/…` (nunca se gasta cupo dos veces); credenciales en
  `<datos>/library-secrets.json` (0600), nunca en respuestas ni logs. **Resincronización**: por hash no se toca (salvo
  `osub_resync=always`); por nombre se resincroniza con `subs.resync` si hay Whisper y el subtítulo está en el idioma del
  audio; se muestra el original mientras tanto y un evento `library-subs` lo cambia; si el alineado es pobre, se queda
  el original.
- ADR-056 · Subtítulos de la web (H29): mpvd baja directamente de las URLs del `-J` en caché (`subs.web.list/fetch`)
  los subtítulos manuales y los automáticos del idioma original (`<lang>-orig`, `kind=asr` sin `tlang`), en SRT nativo
  si la web lo da (YouTube sí) o VTT. No se usa `--write-auto-subs` en ytdl_hook ni en yt-dlp: con `--sub-langs all`
  YouTube expone miles de traducciones (4372 pistas, `-J` de 14 MB, minutos de carga) y sus traducciones automáticas
  (`tlang=`) responden HTTP 429 sin PO token (verificado el 2026-09-30 con estable, nightly y curl_cffi): no se ofrecen;
  la traducción es la nuestra, offline y del archivo entero (`subs.translate`). Las entradas `m3u8_native` de
  `automatic_captions` son la pista manual por HLS: se ignoran. Los automáticos llegan «rodando» (cada cue repite la
  línea anterior; SRT nativo con cues solapados ~2,5 s; VTT con cues de 10 ms y marcas por palabra): se rehacen en
  cues de hasta dos líneas sin solaparse (dos líneas cortas, ≤ 48 caracteres, van en una). SRT en caché
  (`<caché>/subs/web/<url>/<lang>.<tipo>.srt`); las URLs caducan (~6 h): con 403/404/410 se pide el `-J` otra vez.
  mu-subs lo ofrece solo cuando ytdl_hook reprodujo la URL (mu-ytdl `active`).
- ADR-051 · Escritorio y sonido (H24): MPRIS en mpvd (`mpvd/mpris.py`), no el plugin C `mpv-mpris` (habría que
  compilarlo con cabeceras de GLib; sin sudo) ni Lua (no habla D-Bus). Un nombre `org.mpris.MediaPlayer2.mpv_uos.
  instance<pid>` por cada mpv conectado, con su propia conexión IPC y observadores (el bucle de sesiones no cambia);
  `PropertiesChanged` agrupados cada 50 ms, `Seeked` en `playback-restart`, `Position` bajo demanda. D-Bus con
  `jeepney` (Python puro, sin dependencias, extra `desktop` que instalan check.sh e install.sh; mpvd sigue sin
  dependencias obligatorias: sin él, `services.mpris=false`). Los tests usan un `dbus-daemon` privado y la batería
  entera pone `MPV_UOS_MPRIS=0` para no llenar los controles de KDE de reproductores de prueba. Volumen igualado:
  `dynaudnorm` lento con RMS objetivo (`f=500:g=31:p=0.9:m=8:r=0.15`, 7,5 s de anticipación); `loudnorm` descartado
  (remuestrea a 192 kHz, varias veces la CPU) y ReplayGain queda para la música (H32). Ecualizador: 7 perfiles de
  filtros `equalizer` (biquads baratos) con `alimiter` sin nivelado automático tras los realces; el perfil se guarda
  en mu-prefs (`mu-av.eq`).
- ADR-052 · Identidad (H33): `brand.json` en la raíz es la única fuente del nombre (`name`, pendiente de Ser: sigue
  «MPV-UOS»), del identificador de ficheros (`id`), de la carpeta de usuario (`folder`) y de la paleta; lo leen
  `mpvd/brand.py` (con valores por defecto si el archivo falta o está roto), `mu/brand.lua`, `tools/install.sh` y la
  PWA (el servidor sustituye el literal al servir `index.html`, `manifest.webmanifest` y `app.js`). Logo «C · Anillo»
  como icono de escritorio (scalable + simbólico monocromo), de la PWA (SVG + PNG 192/512 generados con rsvg-convert y
  versionados: el móvil los necesita para instalarla) y fuente de la paleta de uosc (`color=` en uosc.conf: azul señal
  en progreso y selección, tinta de fondo). El ámbar marca «en directo / grabando»: el ● REC de mu-record y `--live`
  de la PWA; los botones de uosc no admiten color por estado (su API `set-button` solo tiene icon/active/badge), así
  que ahí el estado se ve con `active` y el contador. Sin bandeja todavía: `mpv-uos-symbolic` queda instalado para ella.
- ADR-053 · Formatos por hardware (H31): `mpvd/hwdecode.py` lee los perfiles con `VAEntrypointVLD` de `vainfo`
  (este portátil: H.264, HEVC Main, VP8, MPEG-2; sin VP9 ni AV1) y clasifica cada formato de `-J` en «fluido en tu
  equipo» / «exigente (por procesador)»; Windows/macOS quedan «desconocido» (sin etiqueta) hasta probarlos
  (`MPV_UOS_HWDECODE` lo fija a mano). Descargas: `-S vcodec:X,res,acodec:opus` con X = el mejor códec decodificado
  por hardware en el orden de yt-dlp (av01 > vp9 > h265 > h264; h264 si no se sabe); «hasta 1080p» es el recomendado
  y el audio original prefiere Opus (`-S acodec:opus`). MP4 forzado en las uniones (`--merge-output-format mp4`, no
  `mp4/mkv`): la tabla de compatibilidad de yt-dlp deja fuera a Opus y caía en .mkv, pero ffmpeg lo mete en mp4 sin
  problema (verificado con 299+251 y con una descarga real); si ffmpeg rechaza los códecs («Conversion failed»), la
  misma descarga se repite una vez en .mkv. Sustituye la parte «mp4 = H.264 + AAC» de ADR-019: la compatibilidad
  total queda para «MP4 compatible» de Convertir y HEVC para «Más pequeño». Reproducción: mientras `ytdl-format` sea
  el de mpv.conf, mu-ytdl pone en su hook `on_load` (prioridad 9) un `ytdl-format` local del archivo con los códecs
  por hardware primero (`[vcodec^=av01]`, `[vcodec~='^(vp0?9)']`…), así mu-prefs no lo aprende como elección del usuario
  y un formato elegido por el usuario se respeta.
- ADR-054 · Compartir: ver juntos (H25): servicio `mpvd/share` con su propio servidor HTTP (puerto 8791, no el del
  mando: la API del mando debe seguir siendo solo LAN y este es lo único que un túnel futuro publicaría). Enlace
  `/s/<sala>#k=<token>`: el token va en el fragmento (nunca en una línea de petición ni en un log), se canjea una vez por
  una cookie firmada con el secreto de la sala; cerrar o caducar (máx. 24 h) invalida todas; límite de intentos por IP
  y por sala. Sincronía por SSE (posición, pausa, velocidad, archivo) y la página corrige su deriva (~0,1 s medido con
  Chrome sin ventana). Qué reproduce el invitado: la URL directa del formato de yt-dlp si el navegador la admite; si
  no, HLS hecho por ffmpeg en la caché (`-c copy` si es H.264/AAC, si no VA-API `h264_vaapi -low_power 1` o libx264
  veryfast) con el subtítulo de texto activo en WebVTT; hls.js 1.7.3 vendorizado para Chrome/Firefox de escritorio.
  Permisos: *solo ver* por defecto, control con aprobación del anfitrión en mpv, revocable; expulsar. El túnel de
  Cloudflare está pendiente (NEEDS_HUMAN): el permiso para exponer el equipo a internet lo tiene que dar Ser.
- ADR-055 · Pistas de los canales y búsqueda por lista (H30): `mpvd/iptv/tracks.py` nombra las pistas con dos fuentes,
  las líneas `#EXT-X-MEDIA` del master HLS (LANGUAGE, NAME, CHARACTERISTICS, FORCED) y el `track-list` de mpv (FFmpeg
  conserva el idioma y `visual-impaired` pero pierde NAME y repite el audio por variante). `qaa` = Versión original,
  `ads`/`qad`/describes-video = Audiodescripción. El título de una pista de mpv es de solo lectura (verificado en 0.41):
  los nombres se usan en el menú propio de mu-iptv y en el OSD. Lo que trae cada canal se guarda al verlo o en la
  comprobación de salud → distintivos CC · VO · AD en las listas. `iptv.search` acepta grupo/país/categoría y los
  ámbitos favoritos, recientes y Radio Browser con el mismo motor sin acentos. mu-prefs no aprende `ads` ni `qaa–qtz`
  como idioma preferido.
- ADR-057 · Suscripciones (H23): `mpvd/subscriptions` (`feeds.*`). Canales y listas con `yt-dlp --flat-playlist -J -I
  1:N` (un canal solo mira sus N más nuevos); podcasts por RSS con caché HTTP (ETag). Nuevo = id nunca visto; la
  primera comprobación solo toma los `initial` más recientes. Descarga por el gestor de descargas, de una en una y en
  prioridad baja, solo si las reglas lo permiten: franja diaria, límite por franja (descargas o MB) y pausa con red
  medida (NetworkManager). Reglas tras cada descarga: conservar N, borrar lo visto tras un margen; solo se borra lo que
  descargó esa suscripción y dentro de sus carpetas. Cadena tras descargar: volumen igualado con `loudnorm` en dos
  pasadas (EBU R128, −16 LUFS; ReplayGain no basta: mpv solo lo lee con `--replaygain`), renombrar con plantilla, mover
  a una carpeta (si es de la biblioteca, se reescanea) y subtítulos IA (+ traducción) en prioridad baja. SponsorBlock
  es opción de la propia descarga de yt-dlp, no un paso de la cadena.
- ADR-058 · Modos de uso (H27): script `mu-modes`. Mini reproductor = `window-scale` a ~30 % del ancho de pantalla,
  `border=no`, `ontop=yes` (en Wayland la posición la decide el compositor). Modo salón = pantalla completa,
  `sub-scale` 1,5, `osd-font-size` 50 y `uosc-scale` 1,8 por `script-opts`; modo sencillo = `uosc-controls` reducido y
  mu-menu muestra 4 categorías (lee `user-data/mu/modes`). Cada modo guarda lo que cambia y lo devuelve al quitarse;
  salón y sencillo se recuerdan (mu-prefs), el mini no. Mando de consola: el mpv del sistema está compilado sin
  `--input-gamepad` (SDL), así que mpvd lee la API de joystick de Linux (`/dev/input/js*`, `struct js_event` de 8
  bytes, disposición del controlador xpad) en un hilo, solo mientras el modo salón está activo, y empuja acciones a
  mu-modes (`mu-event`). Windows/macOS: sin mando (docs/PLATAFORMAS.md).
- ADR-062 · «¿Qué me he perdido?» (H27): resumen extractivo en mpvd (`recap.summarize`), no un LLM local: en el
  hardware objetivo (4 núcleos, sin GPU) un modelo generativo tarda decenas de segundos por párrafo y puede inventar;
  elegir frases del propio diálogo tarda < 1 s y cada frase lleva su minuto (Enter salta ahí). Palabras, por orden:
  subtítulo externo (SRT/VTT/ASS), pista de texto incrustada (extraída una vez con el argv de `subs.extract` a la
  caché), transcripción IA que mpvd ya tenga; nunca se lanza una transcripción nueva. Selección: centralidad respecto
  al centroide con el modelo de embeddings de `semantic` si está instalado, o puntuación por palabras de contenido que
  se repiten (sin tildes, con palabras vacías es/en); en ambos casos MMR (λ 0,7) para no repetir; ~1 frase cada
  2 min (3–7). mu-recap sigue `focused` y `window-minimized`: ausencia = sin foco o minimizada mientras reproduce; una
  pausa cierra el tramo; al volver tras ≥ 60 s de vídeo muestra el aviso con la tecla.
- ADR-063 · Enviar a la tele (H27): DLNA/UPnP AV con la biblioteca estándar (`mpvd/cast`): SSDP `M-SEARCH`
  MediaRenderer:1, descripción del dispositivo (AVTransport:1 y RenderingControl:1), SOAP SetAVTransportURI con
  DIDL-Lite (muchas teles rechazan metadatos vacíos; `DLNA.ORG_OP=01` en archivos para permitir saltos por bytes, `00`
  en directos), Play/Pause/Stop/Seek `REL_TIME`/GetPositionInfo/SetVolume. mpvd sirve el medio en la LAN (puerto 8792,
  solo mientras se envía, token aleatorio por elemento, rangos de bytes en streaming). La tele recibe el archivo tal
  cual si su códec es H.264/HEVC/MPEG-2 + AAC/MP3/AC-3; si no, un relé MPEG-TS de ffmpeg (vídeo copiado si es H.264,
  si no libx264 veryfast; audio AAC; solo audio → MP3); un vídeo web con MP4 progresivo va por su URL directa. Un relé
  no se puede saltar: se reinicia con `-ss` y un token nuevo. El proceso del relé se mata con SIGKILL y se cierra su
  transporte (en Python 3.12 `wait()` no vuelve hasta EOF de la tubería). Chromecast queda fuera por ahora: su
  protocolo (CASTV2: TLS + protobuf + mDNS) pide `pychromecast` y un receptor real para probarlo; se añadiría como extra
  opcional con la misma interfaz `cast.*`.
- ADR-059 · Menú «Suscripciones» (H23): script propio `mu_feeds` (binding feeds-menu, `alt+Y`) abierto también como
  hijo de «Descargas y conversión» (⌫ vuelve allí). Vistas de un solo menú de uosc con mu/nav; los cambios se guardan
  al momento en mpvd y el menú se refresca con los avisos `mu-event` (≤ 4 Hz). Al tocar la cadena de una suscripción se
  envía la cadena completa (la general + el cambio): feeds.update parte de los valores por defecto, no de la general.
  Consecuencia: una suscripción con cadena propia no puede volver a «usar la general» desde el menú (haría falta
  `chain: null`, que `mp.utils.format_json` no escribe); se cambia fila a fila. Aviso OSD solo para comprobaciones
  recientes (< 2 min) con episodios nuevos.
- ADR-060 · Panel de descargas y «Enviar a MPV-UOS» (H23): el panel es otra página del servidor HTTP del mando
  (`/downloads`, mismo puerto, token de un solo uso y cookie HMAC; nada de túnel), con API propia en
  `mpvd/remote/downloads.py` sobre `tasks.list`, `ytdl.download.batch` y `ytdl.downloads.*`/`convert.*` (lista cerrada de
  acciones; «play» solo abre un archivo que escribió una tarea terminada). Vivo por SSE que consulta cada 1 s y solo
  emite si algo cambió. `remote.pair {path, local}` da el enlace al panel; `local` usa 127.0.0.1 porque es contexto
  seguro y ahí el navegador permite notificaciones; por la LAN sin HTTPS no, así que el aviso es banner + vibración +
  título (sin push con la página cerrada). Enlaces `mpv-uos://download?url=<url>[&preset=<id>]`: bin/mpv-uos los pasa a
  `python -m mpvd link` sin abrir el reproductor (cola + notificación del escritorio); solo http/https, uno por enlace,
  porque cualquier web puede disparar el esquema (el navegador pregunta antes). Para ver una página basta el enlace de
  H17 `mpv-uos://open?path=<url>`. El marcador sin esquema abre `/downloads#add=<url>` y solo rellena el recuadro:
  descargar exige pulsar «Descargar». Descartado Web Share Target («Compartir» de Android): exige PWA instalada, que
  exige HTTPS.
- ADR-061 · Compartir: sala pública, chat y «Emitir en directo» (H25, sin túnel). Sala pública «solo ver» (solo red
  local): el enlace lleva `&v=1` y la página entra sola como «Espectador N», sin nombre, hasta un máximo configurable
  (20 por defecto, tope 100); sin control ni chat; los espectadores solo ven cuántos miran y el anfitrión no recibe un
  aviso por cada uno. Si está llena, el sitio de quien lleva más de 30 s desconectado pasa al nuevo. Caducidad y límite
  de intentos iguales que en las privadas. Chat y reacciones solo en salas privadas, por el mismo SSE (evento `chat`):
  200 caracteres, 5 mensajes y 8 reacciones cada 10 s por invitado (429), texto limpio de caracteres de control y bidi
  que la página pinta siempre como texto (`textContent`) y mu-share escapa para ASS; en mpv, unas líneas abajo a la
  izquierda durante `chat_seconds` (máximo 10 Hz), las reacciones con palabras porque libass no pinta emoji de color.
  «Emitir en directo»: ffmpeg a RTMP/RTMPS con H.264 de hasta 720p y 30 fps a 2500 kb/s y un fotograma clave cada 2 s,
  AAC 128k a 44,1 kHz, FLV; VA-API si hay (como convert) con reintento por CPU; `-re` y la posición del anfitrión para
  archivos, directos tal cual; imagen negra o silencio si falta una pista. Desactivado hasta configurarlo; servidor y
  clave en `<datos>/live.json` 0600. La clave nunca sale por la API, los registros ni el OSD y el menú no la ve: mpvd la
  lee del portapapeles de mpv. Límite conocido: ffmpeg necesita la URL completa como argumento, así que mientras emite
  la clave está en su línea de órdenes (`/proc/<pid>/cmdline`); ffmpeg 8 no carga de archivo opciones de protocolo como
  `-rtmp_playpath` (comprobado). Parar mata solo su PID; también se para al cerrar el reproductor que la empezó o mpvd.
  Sin reconexión automática si se corta.
- ADR-067 · AppImage (H28): `tools/build_appimage.sh` empaqueta lo que git versiona (bin, mpv-config con uosc y sus
  binarios, mpvd, brand.json), el CPython 3.12 independiente que gestiona uv (python-build-standalone, reubicable) con
  los extras ligeros (`desktop` por defecto) y el yt-dlp vendorizado; `.venv/bin/python` es un enlace relativo a ese
  Python, así que mu-core y bin/mpv-uos no cambian. mpv NO va dentro: se usa el del sistema (≥ 0.41), igual que en el
  checkout; meter mpv con ffmpeg, libplacebo y sus drivers multiplicaría el tamaño y rompería la aceleración por
  hardware del sistema (AppRun avisa con la orden para instalarlo). Whisper y los modelos se quedan fuera (cientos de
  MB, se bajan bajo demanda). La imagen es de solo lectura: AppRun manda la caché a XDG y `MPV_UOS_VENDOR_BIN` a
  `<datos>/bin` (la copia de yt-dlp que la actualización diaria reemplaza; mpvd y mu-ytdl la leen). appimagetool 1.9.1
  fijado por SHA-256 en vendor.lock (la release no publica sumas). Flatpak descartado por ahora: habría que compilar
  mpv y ffmpeg dentro del SDK (horas en 4 núcleos) o depender de extensiones; el AppImage (41 MB) cubre el caso
  «descargar y abrir». ARM64 sin probar (hace falta la máquina; NEEDS_HUMAN).
- ADR-065 · Letras, audiolibros y «¿Qué canción es?» (H32): letras en mpvd (`lyrics.get`): `.lrc` junto a la canción,
  etiqueta de letra (LYRICS/UNSYNCEDLYRICS, `lyrics-<idioma>` de USLT, `©lyr`) leída con ffprobe y, solo si el usuario
  lo enciende, LRCLIB (`GET /api/get`, verificado; respuestas y fallos en caché 7 días). Se muestran como pista de
  subtítulos «Letra» (LRC → SRT en la caché, `sub-add`), no como overlay: ocultar (`v`), retardo y estilo ya existen; si
  mpv ya cargó el `.lrc` (sub-auto incluye `lrc`) no se duplica. Carátulas: mpv ya carga cover/folder/front… por
  defecto (`cover-art-auto=exact`, `cover-art-whitelist`). Audiolibros en mpvd (`books.*`, `<datos>/books.json`): libro
  = m4b/aa/aax, género de libro, > 1 h, o carpeta de ≥ 3 pistas del mismo álbum de > 1 h en total con mediana ≥ 8 min o
  pista en género/nombres (un disco largo no es un libro); el usuario lo fuerza en ambos sentidos. Podcast = género
  Podcast: posición por episodio y velocidad heredada del programa. Solo posición (pista + tiempo), velocidad,
  marcadores con nota y «terminado»: sin historial de escuchas. mu-books aplica la velocidad como
  `file-local-options/speed` (vuelve la normal al cambiar de archivo y mu-prefs no la aprende); el temporizador cuenta
  tiempo de reproducción y baja el volumen en los últimos 20 s. Identificar canciones (`songid.*`): Chromaprint `fpcalc`
  + AcoustID solo con interruptor y clave propia del usuario (0600, `songs-secrets.json`), apagado por defecto;
  «Guardar en el archivo» reescribe etiquetas con `ffmpeg -c copy` y reemplazo atómico, solo bajo petición.
- ADR-064 · Música (H32): biblioteca en mpvd (`music.*`, `<datos>/music.sqlite3`), separada de la biblioteca de vídeo
  (H22) porque su modelo es otro (artista/álbum/género/año, pistas y discos). Carpeta Música de XDG por defecto
  (`MPV_UOS_MUSIC_DIR` la cambia), escaneo incremental por mtime/tamaño con ffprobe a prioridad baja, carátulas del
  archivo o de la carpeta. Volumen igualado: `replaygain` de mpv con las etiquetas; sin etiquetas, mpvd mide ReplayGain 2.0
  (`ebur128`, pico de muestra) en segundo plano y mu-music lo aplica como `replaygain-fallback` local del archivo; nunca se
  escriben los archivos. Fundido: mpv reproduce un solo flujo, así que no hay fundido cruzado real: se baja y sube
  `volume-gain`. Sin cortes: `gapless-audio=yes` + `prefetch-playlist=yes`. Listas en M3U8 (UTF-8) en los datos del
  usuario; listas inteligentes calculadas; historial local, borrable y nunca enviado (no es scrobbling). Salida exclusiva
  con `audio-exclusive` (solo PipeWire/WASAPI/CoreAudio). Perfiles genéricos por tipo de auricular en mu-av (sin
  mediciones por modelo). Tecla `alt+M`.
- ADR-066 · Windows (H28): lanzador e instalador en PowerShell (`bin/mpv-uos.ps1` + `.cmd`, `tools/install.ps1`), compatibles
  con Windows PowerShell 5.1 (viene con el sistema) y pwsh 7, sin administrador: accesos en el menú Inicio y `mpv-uos://` en
  `HKCU`. Binarios de Windows fijados en `vendor.lock` y verificados por SHA-256 (`yt-dlp.exe` de la misma release que el de
  Linux, whisper.cpp CPU oficial `b5130`); sin Git Bash ni `tools/vendor.sh`. mpvd: named pipes con el lazo Proactor y bloqueo
  de arranque con `msvcrt.locking` (acotado a 30 s). Como no hay Windows aquí, se prueba en Linux con un pwsh 7 portátil
  (`.cache/pwsh`, bajado por el test @network): parser real, `-DryRun` idéntico a `bin/mpv-uos`, descargas contra un servidor
  local e instalar/desinstalar en una carpeta temporal. La prueba en un Windows real queda en NEEDS_HUMAN.md.
- ADR-069 · Reparto de CPU entre descargas, transcripción y traducción (H34). El problema: un trabajo de la cola de mpvd
  dura todo lo que dure su tarea, y la cola tiene un worker por núcleo menos uno (tres en este portátil). Las descargas,
  que duran minutos, llenaban la cola y los trabajos urgentes (subtítulos IA en vivo, traducir, clips) no arrancaban: el
  sistema de prioridades quedaba anulado. Decisión: las descargas esperan en una cola propia del gestor
  (`DownloadManager._pending`, como ya hacía `ConvertService`) y solo se manda a la cola de trabajos lo que de verdad va a
  correr, como máximo «descargas a la vez». Lo que espera se ve igual en el panel y cuenta como activo.
  Lo que NO se ha hecho, y por qué: un semáforo que limite los trabajos `heavy` simultáneos parecía la solución obvia para
  que whisper (hilos = núcleos-1) y la traducción no pidan a la vez más hilos que núcleos hay. Se ha descartado porque
  provocaría un bloqueo mutuo: la cadena tras descargar (`chain.*`, H23) es un trabajo `heavy` que espera desde dentro a
  otros trabajos `heavy` (`asr.*`, `subs.translate`), así que el permiso nunca se liberaría. En su lugar, la traducción
  (Argos y Opus) usa la mitad de los núcleos en vez de todos menos uno, que era el caso que se junta de verdad con la
  transcripción (el menú de subtítulos invita a hacer las dos cosas, y la cadena hace las dos). Si algún día se quiere el
  semáforo, antes hay que sacar la orquestación de la cadena de un trabajo `heavy`.
- ADR-068 · Entrar en una sala desde internet con un túnel rápido de Cloudflare (H25). Problema: una sala de «ver juntos»
  solo servía dentro de casa; para que entre alguien de fuera habría que abrir un puerto en el router (ni se puede hacer
  desde aquí ni se le va a pedir a Ser). Decisión: `cloudflared tunnel --url http://127.0.0.1:<puerto>`, el túnel rápido
  que no necesita cuenta ni configuración y que devuelve una dirección `https://<palabras-al-azar>.trycloudflare.com`
  viva solo mientras vive el proceso. Se arranca al abrir la sala y se mata al cerrarla, así que nada del reproductor es
  alcanzable desde internet ni un segundo más que la sala; la dirección es nueva cada vez y el enlace sigue llevando el
  token de invitación, que es lo único que deja entrar. Está **apagado** salvo que Ser lo encienda en *Compartir → Que se
  pueda entrar desde internet* (se recuerda en mu-prefs), y el binario **no** se instala con el resto: solo con
  `MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh`, fijado y verificado por SHA-256 en vendor.lock (Cloudflare no publica
  ficheros de sumas, así que la suma es la del binario verificado aquí el 2026-10-01: protege de cambios posteriores, no
  es una firma del fabricante). Si falta el binario o el túnel falla, la sala se abre igual en la red local y el menú
  dice por qué. Ventaja añadida: con túnel no hace falta tocar el cortafuegos, así que el aviso de `ufw` desaparece.
  Alternativas descartadas: UPnP/abrir puertos en el router (frágil y expone la casa), un relé propio (necesitaría un
  servidor y rompería el «local-first»), ngrok y similares (requieren cuenta).
- ADR-070 · Fuera los subtítulos IA «en vivo»: se prepara el archivo entero antes de verlo (H36). **Sustituye** a la
  parte de ADR-024 y ADR-042 que daba por bueno transcribir mientras mpv decodifica (el resto de los dos sigue en pie:
  un proceso por trozo y los tiempos por token). El problema: el modo en vivo
  obligaba a elegir el modelo por la velocidad y no por la calidad (en un portátil de 4 núcleos, `base`), y `base`
  transcribe con faltas y casi sin puntuación, que es justo lo que estropea después la traducción; además, con el
  look-ahead persiguiendo la posición, el texto aparecía a trozos y cualquier salto adelante dejaba hueco. Medido
  (docs/BENCHMARKS.md, 2026-10-01): `small-q8_0` da RTF ≈0,45 con trozos de 28,5 s, o sea que transcribe más del doble
  de rápido que el vídeo; `medium-q5_0` 4,03 y `large-v3-turbo-q5_0` 5,22. Decisión: un solo modo, `prepare`, que
  arranca en el segundo 0 y recorre el archivo en orden con el mejor modelo que aguante el equipo
  (`TIER_PREPARE` en mpvd/asr/models.py: small-q8_0 en ≤4 núcleos, medium-q5_0 de 5 a 8, large-v3-turbo-q5_0 por
  encima), y `precompute` para el siguiente de la lista. Desaparecen el propósito `live`, el cursor de look-ahead y el
  troceado según la posición de reproducción. Consecuencia incómoda y asumida: hay que esperar. Para que la espera no
  sea a ciegas, mpvd publica en cada tarea `rtf_recent` (ritmo de los últimos trozos), `remaining` (lo que queda, a ese
  ritmo) y `alt` (lo que tardaría con `small-q8_0`), y mu-subs lo dice con palabras: «listos en 7 min · va más rápido
  que el vídeo, no te alcanzará», o con un modelo lento, «listos en 1 h 10 min · con small-q8_0, 9 min». Se recalcula
  solo: si la máquina se carga, el ritmo sube y el aviso sube con él. En TV y radio no hay subtítulos IA: solo los que
  manda el canal (las pistas de ADR-055). Alternativas descartadas: seguir en vivo con `base` (mala calidad y el peor de los dos
  mundos); transcribir en vivo solo el trozo que se está viendo (es lo que ya hacía y es lo que falla al saltar);
  esperar sin decir cuánto (es lo que convierte una espera razonable en un programa roto).
- ADR-071 · Cascada de proveedores de subtítulos y qué proveedores NO se ofrecen (H36/C5). `subs.find {path}` devuelve
  en una sola lista todo lo que hay para ese archivo o esa URL, ordenado por **fiabilidad**: `canal` (los que trae el
  propio sitio del vídeo, que son de ESE vídeo) · `hash` (OpenSubtitles reconoce el archivo exacto) · `nombre` (coincide
  el título: puede ser otra versión y descuadrar) · `auto` (subtítulos automáticos de la web, transcripción de máquina);
  a igual fiabilidad manda el orden de idiomas preferidos, luego lo no automático y luego las descargas.
  `subs.pick {source}` se come el campo `pick` tal cual, así el menú no tiene que saber qué RPC toca para cada
  proveedor. La respuesta incluye además **todos** los proveedores con `ok` y `reason`: decir «no hay subtítulos» cuando
  lo que falta es una clave es mentir, y era justo lo que pasaba antes. Comprobado contra los servicios reales el
  2026-10-01, lo que cambió el plan: **Podnapisi está descartado** (su dominio da NXDOMAIN en el resolutor del sistema y
  en 1.1.1.1: no hay a dónde conectarse); **Subdl queda pendiente de una clave gratuita** (sin ella la API responde
  `403 not_authorized`, y aquí no se escribe un cliente contra una API que no se ha podido ver funcionar: anotado en
  NEEDS_HUMAN.md); y el **API antiguo de opensubtitles.org** (`xml-rpc`), que no necesita clave, se descarta como
  proveedor porque las descargas anónimas devuelven un fichero de 102 bytes con un anuncio en vez del subtítulo
  (comprobado con cuatro subtítulos cuyo `SubSize` era ~155 KB), aunque la búsqueda sí funcione y el límite sea generoso
  (200 al día por IP). Consecuencia: los proveedores reales son dos (la web del vídeo y OpenSubtitles.com) y el menú lo
  dice; añadir un tercero es rellenar una tupla y escribir su `pick`.
- ADR-072 · Descargar por una sola puerta (H37). Antes había dos filas distintas —«Descargar varias URL…» y «Descargar de
  una lista o canal…»— y había que saber de antemano qué era lo que ibas a pegar; además el formato era uno para todo el
  lote. Ahora hay una sola fila, **Descargar…**, y lo que se pega se clasifica en el propio menú: una ruta de `.txt` (se
  leen sus enlaces, saltando comentarios `#`/`;`), una URL (se le pregunta a mpvd con `ytdl.playlist`: si trae entradas es
  una lista o un canal, y si no, se trata como un enlace suelto) o varias URL. En los tres casos se acaba en **la misma
  lista con casillas**: la primera fila es el formato de todos (recordado en mu-prefs), la segunda el SRT aparte, y cada
  fila puede llevar su propio formato y su propio SRT con sus acciones (uosc las recorre con Tab y las dispara con Enter,
  docs/UOSC_API.md §2.3). Al descargar, las filas marcadas se agrupan por (formato, SRT) y va **una llamada a
  `ytdl.download.batch` por grupo**; si es una lista entera sin nada propio por filas se usa el camino de siempre, que
  además numera y mete todo en una carpeta con el nombre de la lista. Alternativas descartadas: una llamada
  `ytdl.download` por fila (ruido en la cola y pierde el archivo de «ya descargado» por lote) y pedir el formato una vez
  por fila (es lo que hacía la gente abandonar a mitad). Lo que yt-dlp no puede abrir se dice antes de intentarlo
  (`ytdl.site_support`, `mpvd/ytdl/sites.py`): los **guardados de Instagram** y los **favoritos de TikTok** no tienen
  extractor —comprobado el 2026-10-01 preguntando a los extractores del binario instalado— así que se rechazan con su
  alternativa; el perfil de Instagram se ofrece con el aviso de que yt-dlp lo marca roto; y los avisos de «esto es
  privado: hace falta tu sesión del navegador» salen solos para perfiles y colecciones.
- ADR-073 · SponsorBlock al reproducir, sin decir qué estás viendo, y el espejo que no suena (H39). **SponsorBlock**: los
  tramos marcados por la gente ya se usaban al descargar (`--sponsorblock-remove`) pero no al ver, que es cuando molestan.
  `mpvd/sponsorblock.py` los pide por un **prefijo de 4 caracteres del sha256 del id** del vídeo
  (`GET /api/skipSegments/<prefijo>`), que devuelve los de todos los vídeos con ese prefijo —106 en la prueba del
  2026-10-01— y el filtrado por vídeo se hace aquí: lo único que sale del equipo son 4 caracteres hexadecimales, que valen
  para 1 de cada 65 536 vídeos. Descartado el endpoint directo por id (le dice al servicio qué estás viendo) y descartado
  bajar la base entera (gigabytes). Se saltan por defecto patrocinio, autopromoción, «suscríbete» y partes sin música;
  cabecera y despedida **no**, porque hay quien las quiere. Los tramos van a la misma lista que la intro y los créditos de
  mu-intro: mismo botón, misma tecla, mismo salto automático con cuenta atrás, y `actionType` distinto de `skip` (mute,
  poi) se ignora en vez de inventarle un comportamiento. **El espejo que no suena**: el salto a la copia siguiente de un
  canal se disparaba solo con `end-file reason=error`, y las radios caídas no dan error: se quedan conectando. Ahora, si a
  los 8 s (`mu-iptv-stall_seconds`) el reloj no ha avanzado y mpv sigue en espera, se pasa a la siguiente copia igual que
  si hubiera fallado. **Y la lista dice lo que se sabe**: el resultado de la comprobación se enseña con palabras («✕ no
  responde», «prohibido: suele ser geobloqueo») y, en un canal repetido, cuántas de sus copias respondieron, en vez de un
  «✕» que no decía si merecía la pena intentarlo. **Intro**: el análisis para en cuanto **dos** vecinos coinciden en los
  dos tramos (dos es lo que ya se exigía para dar un tramo por bueno), así que se ahorra la huella del tercero.
- ADR-074 · Despertar para grabar y apagar al terminar (H40). Lo que se puede hacer sin pedir contraseña, comprobado en
  este equipo el 2026-10-01: **suspender y apagar sí** (logind contesta `yes` a `CanSuspend`/`CanPowerOff` para la sesión
  local, así que basta `systemctl suspend|poweroff`), **poner el despertador no** (`rtcwake` da «/dev/rtc0: Permiso
  denegado» y `sudo -n` pide contraseña). Decisión: el programa **detecta** lo que puede hacer y **no instala nada**; para
  el despertador dice la orden exacta de sudoers, limitada a `rtcwake` (o `pmset` en macOS), y la grabación se programa
  igual anotando «sin despertador: …» — no se pierde una grabación porque falte un permiso. Descartados un helper con
  setuid (una puerta abierta para siempre por una alarma) y pedir la contraseña al usuario desde el reproductor (un
  programa que pide la contraseña de root enseña a la gente a dársela a cualquiera). El despertador solo saca de la
  **suspensión**: encender un equipo apagado es cosa de la BIOS. Mientras graba, el ffmpeg va envuelto en
  `systemd-inhibit --what=sleep:idle` (en macOS `caffeinate -i`). Antes de suspender o apagar hay **tres seguros** —nadie
  usando el reproductor, ninguna grabación a menos de 15 min, nada descargando/convirtiendo/transcribiendo— y un aviso de
  60 s con botón *Cancelar*; los seguros se vuelven a mirar **después** del aviso, porque en un minuto puede haber vuelto
  alguien. `MPVD_POWER_FAKE` sustituye todas las órdenes de energía por un programa que solo apunta lo que se le pidió:
  los tests comprueban la orden exacta de cada plataforma sin tocar el equipo. Las dos opciones («despertar 5 min antes»,
  «al terminar: nada/suspender/apagar») las guarda **mpvd**, no el reproductor, porque tienen que valer con el reproductor
  cerrado, y se heredan en cada grabación nueva.
- ADR-075 · Índice del vídeo: secciones y frases clave con su minuto, sin IA generativa (H38, nivel 1). **Amplía**
  ADR-062 (el «¿qué me he perdido?» extractivo) de un tramo al vídeo entero, y prepara el nivel 2. `recap.outline` parte
  del subtítulo que ya hay —el de la web, el descargado, una pista interna o la transcripción que mpvd ya tenga— y
  **nunca lanza una transcripción**: hacerlo para un índice son horas en el portátil objetivo, así que cuando no hay
  palabras se dice, en vez de poner a la gente a esperar. Los cortes entre secciones salen del mismo detector de cambio
  de tema que los capítulos (`semantic.index.chapters`, embeddings multilingües) y, cuando ese modelo no está o el vídeo
  no cambia de tema, de tramos de 5 minutos: una respuesta peor pero honesta y al instante. Dentro de cada sección, las
  frases clave se eligen con la centralidad + MMR que ya usaba el recap. Los títulos **son** frases del diálogo
  recortadas, no texto escrito por un modelo, así que los minutos no hay que validarlos: son los del subtítulo.
  Para el nivel 2 (prosa escrita por un modelo local) queda `recap.marks`, que comprueba cada `[mm:ss]` contra el
  subtítulo y lo mueve a la frase más cercana (±30 s) o **lo quita**: una marca que lleva a un sitio donde no pasa nada
  es peor que no tener marca, porque parece que el programa miente. En el reproductor es un menú (`alt+I`): cada sección
  es un submenú y cada línea salta a su minuto.
- ADR-076 · Resumen en prosa con un modelo local (H38, nivel 2). **Sustituye** la parte de ADR-062 que dejaba el LLM
  fuera a propósito: Ser aceptó el coste («se instala al pedirlo») y el nivel 1 se queda como respuesta instantánea, así
  que ahora hay dos niveles y el de siempre sigue siendo el rápido. Medido aquí el 2026-10-01 con 15 min de una charla
  real en español (docs/BENCHMARKS_LLM.md): **gemma-3-1b-it Q4_K_M** (806 MB) tarda 36-45 s y escribe español correcto;
  **qwen2.5-3b** (1,9 GB) escribe mejor pero tarda el doble y queda como opción; **qwen2.5-1.5b** se descartó tras
  medirlo porque repetía frases enteras. Lo que decidió el diseño, y que solo se vio midiendo: (1) **ningún** modelo se
  inventó un minuto cuando el prompt lleva el índice delante, pero (2) hay que pedir explícitamente «cada frase EMPIEZA
  con su marca», porque sin eso gemma escribía el resumen corto **sin ninguna marca** —y un resumen sin minutos no sirve
  para lo que se quiere—; (3) algún modelo escribe rangos `[0:04-0:36]`, que se quedan en su instante inicial porque el
  menú solo puede llevarte a un sitio. Implementación: `llama.cpp` vendorizado desde la release oficial de CPU
  (`tools/vendor_llama.sh`, fijado en vendor.lock), **un proceso por resumen** (`llama-cli -st`) en vez de un servidor
  (40 s cada pocas horas no justifican 1 GB de RAM y un puerto abiertos todo el día: el mismo razonamiento de ADR-024),
  modelo descargado al pedirlo y verificado por SHA-256 (ADR-023), caché por (índice, longitud, idioma, modelo), y
  `tools/install.sh --resumen` para quien lo quiera desde el principio. Aviso para quien actualice llama.cpp: su
  interfaz de chat mezcla en la misma salida el cartel, el eco **recortado** del prompt y las estadísticas, así que
  `mpvd.llm.clean_output` corta por ahí y hay un test que fija ese contrato.
- ADR-077 · Una sola puerta para abrir y descargar, y un único ayudante de portapapeles (H42). **Amplía** ADR-072, que
  ya había unificado las dos puertas de *descargar*; ahora se unifican también las de *abrir*. Antes había seis filas
  para lo mismo —*Abrir archivo*, *Abrir URL…*, *Pegar URL o ruta copiada*, *Buscar en YouTube*, *Descargar…* y
  *Suscripciones*— y había que saber de antemano qué ibas a pegar para elegir la correcta. Ahora hay **una caja**
  (`mu-ytdl`, vista `gate`, tecla `ctrl+o`, primera fila de *Abrir o descargar* en el menú principal) que clasifica lo
  que se escribe o se pega: varios enlaces, un enlace, un enlace sin esquema (`youtu.be/…`), una ruta de archivo, una
  carpeta, un `.txt`/`.list`/`.urls`/`.csv` con enlaces, o —con la caja vacía— el portapapeles entero, no solo su
  primera línea. Detrás hay **una sola pregunta**, reproducir o descargar, que dice cuántos elementos hay; descargar
  cae en la pantalla de casillas de ADR-072 sin cambiarla. Decisiones que no son obvias: (1) **a mpvd solo se le
  pregunta cuántos elementos trae si la URL huele a lista o a canal** (`list=`, `/playlist`, `/@algo`, `/channel/`,
  `/c/`, `/user/`, `/videos`, `/streams`, `/podcasts`): un vídeo suelto no necesita una llamada de red para decir «1
  elemento», y así *Reproducir* no espera a nada; si se equivoca, la pantalla de descarga lo resuelve igual porque ya
  preguntaba. (2) La respuesta de `ytdl.playlist` **se guarda** y se le pasa a la lista con casillas, así que una
  lista se resuelve una vez, no dos. (3) **La extensión y el tamaño se miran antes de leer el archivo**: el código
  anterior le pasaba cualquier ruta existente a `read_links`, que leía el fichero entero buscando `https://` — con una
  película de 4 GB eso eran 4 GB en memoria. Ahora solo se leen listas de texto y hasta 4 MB. (4) Para un archivo que
  ya está en el equipo, *Descargar* no se esconde: lleva a **convertir**, que es lo mismo dicho bien. (5) *Abrir
  archivo* (`o`, el selector de uosc), `ctrl+u`, `ctrl+f` y `ctrl+v` **siguen existiendo como teclas**: se retiran del
  menú, no del programa. (6) `ctrl+o` pasa a ser *Abrir o descargar* —la tecla que todo el mundo espera para «abrir»—
  y la carpeta de configuración se va a `ctrl+alt+o`. Lo que queda en *Abrir o descargar* además de la caja no son
  puertas, son sitios donde mirar: Biblioteca, Música, Suscripciones (que sale de la raíz de las descargas),
  Recientes y la lista de reproducción. **Portapapeles**: el mismo ayudante estaba escrito **tres veces**
  (`mu-iptv:278`, `mu-remote:236`, `mu-share:648`), y solo la copia de mu-iptv tenía el respaldo de
  `wl-copy`/`xclip`/`xsel`/`pbcopy` cuando mpv no trae backend; ahora hay un único `mu/clip.lua`
  (`clipboard/text` de mpv 0.41 primero, respaldo asíncrono después) que usan los cuatro sitios donde hay un enlace
  que llevarse a otro aparato: la sala, el mando del móvil, la orden del cortafuegos y el panel web de descargas
  (Tab sobre su fila lo copia, con la URL de la red local, no la de `127.0.0.1`).
- ADR-078 · La pila del menú no se borra cuando uosc *sustituye* un menú por otro (arreglo encontrado al hacer H42).
  `mu-menu` observaba `user-data/uosc/menu/type` y, al verlo en `nil`, olvidaba su pila **en el acto** para que una
  respuesta de mpvd que llegara con retraso no reabriera lo que el espectador acababa de cerrar. Pero uosc, al cambiar
  de menú, **destruye el viejo antes de crear el nuevo** (`Menu:destroy` pone la propiedad en `nil` y el
  `Menu:init` siguiente la vuelve a poner), y con la máquina cargada ese `nil` intermedio sí se observa. Consecuencia
  real: al volver de un módulo al menú principal la pila quedaba vacía con el menú abierto, y el siguiente `⌫`
  **cerraba todo en vez de subir un nivel**. Ahora el `nil` solo levanta una bandera (`closing`) que impide reabrir
  nada hasta que se abra a propósito, y el borrado de la pila espera los mismos 0,2 s que usan los demás módulos,
  comprobando al vencer si uosc tiene un menú puesto. Se veía como un test intermitente (`test_nav`), no como un
  fallo, y por eso llevaba tiempo apuntado como «sensible a la carga».
- ADR-079 · Grabar: el envase se elige y se recuerda, programar deja de estar escondido y la radio se puede programar
  (H43). **Qué se puede elegir y qué no**: mpv graba con `mp.set_property('stream-record', file)`, es decir **escribe
  el flujo tal cual**, sin recodificar, en el contenedor que implique la extensión. Así que una fila de «calidad» sería
  mentira; lo que hay es el **envase**: *igual que el original* (MKV, el valor por defecto, el que nunca falla),
  *MP4 si los códecs lo permiten* y *solo el audio* (Opus 128, que ya extraía mpvd con `record.audio`). La elección se
  recuerda en mu-prefs. Medido el 2026-10-01: un **VP8 no entra en MP4** (`Could not write header (incorrect codec
  parameters?)`, y el fichero se queda vacío), y sí entra en WebM copiando (9,64 s para 115 min); por eso cuando se
  pide MP4 y los códecs no caben se avisa y se graba en MKV en vez de dejar un fichero roto. La fila dice de antemano
  si caben («estos códecs caben» / «estos códecs NO caben: se grabaría en MKV»), que es la única forma de que la
  elección sea informada. **Consecuencia que conviene saber**: un recorte de un archivo local elegía MP4 **solo** si
  los códecs cabían, sin decirlo, porque en MP4 el corte empieza exactamente en la marca (lista de edición sobre el
  arranque desde el fotograma clave anterior); ahora eso es la opción *MP4* y el valor por defecto es MKV, que empieza
  en el fotograma clave. Se dice en la fila, y así lo decide quien graba y no el programa. Al desaparecer el envase
  como pregunta, «Grabar solo el audio desde ahora» y «Guardar solo el audio del tramo» dejan de ser filas aparte:
  eran la misma elección contada dos veces. Las teclas (`record-audio-toggle`) y el mensaje
  `mu-record-start audio` siguen valiendo. **Programar** («Programar una grabación…») pasa al primer nivel de *TV y
  radio* y al menú de *Grabar*, que la abre como hija de mu-iptv (entrada `tv-schedule-new`); antes solo se llegaba con
  Tab dentro de la lista de un canal. **La radio**: el análisis apuntaba a dos condiciones, `mu-iptv:449` y `:1042`.
  Comprobado: **la de :449 no era la culpable** —es el filtro del lote de `iptv.epg.now`, y la radio no tiene guía, así
  que quitarla habría hecho pedir la EPG de cientos de emisoras para nada— y la acción de Tab nunca estuvo escondida
  (las acciones van en `item_actions` del menú, iguales para todas las filas). La que lo impedía era la de
  `views.sched_new`, que excluía la radio del selector de canal; es la que se quita. mpvd ya graba audio sin cambios
  (`mpvd/iptv/schedule.py` escribe `.mka` cuando `kind == 'radio'`).
- ADR-080 · Un submenú de uosc necesita un `id` propio cuando su título sale de datos (encontrado al hacer H43).
  uosc deriva el id de un submenú de **su título** (`elements/Menu.lua:192`:
  `menu.id = parent .. ' > ' .. (menu_data.title or i)`), así que dos filas hermanas con el mismo título comparten id:
  `by_id` se queda con la última y, al recalcular los altos, el `set_scroll_to` de la primera busca por id y encuentra
  la otra, cuyo `scroll_height` todavía no está puesto → `clamp(0, pos, nil)` y **uosc revienta al pintar**, dejando
  el menú inservible. Reproducido: dos grabaciones programadas **del mismo canal** (que es lo normal: la radio de las
  mañanas dos días seguidos). No es un caso raro: pasa igual con dos programas del mismo título en la guía (una
  reposición) o dos secciones iguales en el índice del vídeo. Su API pública admite un `id` explícito
  (`if menu_data.id then menu.id = menu_data.id`), así que no hace falta tocar uosc: donde el título de una fila con
  submenú sale de datos se le pone un id estable —`sched:<id>`, `prog:<inicio>`, `sec:<n>`—. **Regla para lo que
  venga: una fila con `items` cuyo título no sea un literal lleva `id`.** De paso, un refresco de «Grabaciones
  programadas» (una grabación que empieza o acaba mientras se mira la lista) ya no la deja en «Cargando…»: parpadeaba.
- ADR-081 · El resumen en los vídeos de internet, y «Resumen e índice» donde se encuentre (H45). **Amplía** ADR-062
  (el resumen) y ADR-075 (el índice), y **se apoya en** ADR-056 (los subtítulos de la web) sin cambiarlo.
  **La causa, verificada**: `mu-recap`'s `source_params()` necesita una pista de subtítulos cargada y para una URL
  acababa en `if path and not is_url(path)` → `nil`. El resumen no estaba roto: se quedaba **sin material**. Lo que
  no se ha hecho, a propósito: **no** se añade `ytdl-raw-options` con `write-auto-subs` a `mpv.conf`, que es lo
  primero que se piensa. Con `--sub-langs all` el `-J` de YouTube pasa de unos KB a **14 MB** (miles de pistas
  traducidas) y eso lo paga el `ytdl_hook` de mpv en cada vídeo que se abra (ADR-056 ya lo midió). En su lugar se usa
  el camino que ya existe: `subs.web.list` / `subs.web.fetch`, que piden el `-J` normal (en caché) y bajan **una**
  pista como SRT limpio. **Cuándo**: al pulsar, no al abrir el vídeo, con el aviso «Buscando los subtítulos del
  vídeo…»; así no hay una petición de red por cada cosa que Ser abra, y el SRT queda en caché para la siguiente vez
  (el test comprueba que la segunda pulsación no toca la red). Medido el 2026-10-01 sobre un vídeo de 15 min:
  **5,41 s** y 23 KB / 3.678 palabras, frente a los ~8 min de transcribir con Whisper. **Qué idiomas**: solo los
  **nativos** del vídeo, y eso lo garantiza mpvd —`list_tracks` ofrece las pistas manuales y las automáticas del
  idioma original, y **nunca** las traducciones automáticas de YouTube, que responden **HTTP 429** sin PO token—.
  Se coge la primera de la lista, que ya viene ordenada: el idioma del usuario antes que el original y manual antes
  que automática. Si la que se usa no está en castellano se dice en una fila («Estos subtítulos están en Inglés ·
  traducirlos al español sin conexión») que lleva al panel de subtítulos, donde la traducción con OPUS-MT ya vive con
  su progreso y su caché: traducir un SRT entero es un trabajo largo y no se duplica aquí.
  **Dónde se encuentra** (D3): «¿Qué me he perdido?» estaba en el **tercer** nivel, dentro de «Herramientas» (15
  filas), y el índice del vídeo **no estaba en el menú** en absoluto, solo como `alt+I`. Ahora las dos cosas son una
  entrada, **«Resumen e índice»** (`mu-recap`, entrada `recap-menu`), que aparece: en la **raíz** del menú principal,
  en el **panel de subtítulos** (que es de donde sale el material) y en *Subtítulos*. Sale de «Herramientas». En la
  raíz va como **fila del archivo que se está viendo**, no como novena categoría: lo que solo sirve para este vídeo no
  ocupa un sitio fijo (criterio de §B.3), así que con el reproductor vacío no aparece y la raíz sigue teniendo ocho
  categorías. `alt+R` y `alt+I` siguen llevando directamente a cada cosa.
- ADR-082 · La sala sirve el fichero original, y el relay empieza donde va el anfitrión (H44). **Matiza** ADR-054 en
  un punto, «qué reproduce el invitado»; todo lo demás de ADR-054 sigue en pie tal cual (token en el fragmento,
  cookie firmada de la sala, SSE, permisos, sala pública). Todo lo que lleva un número aquí está medido en este
  portátil el 2026-10-01, no estimado.
  **C1 · el fallo real.** `mpvd/share/service.py::_file()` hacía `data = path.read_bytes()` y cortaba el rango sobre
  ese buffer: una película de 4 GB habrían sido **4 GB de RSS por petición**. No se notaba porque solo pasaban por
  ahí segmentos HLS de 4 s, pero servir el fichero original lo convertía en un problema de verdad. Ahora se lee por
  trozos de 256 kB mientras se escribe la respuesta (`Response.stream`, que ya existía para el SSE), poniendo el
  `Content-Length` a mano porque con `stream` el servidor no lo pone solo. El Range se conserva **exactamente** como
  estaba (206 con `Content-Range`, `Accept-Ranges`, el sufijo «últimos N», 416 con `bytes */size`): solo cambia de
  dónde salen los bytes. Medido con un MKV de 428 MB servido a mpv: **2.052 kB de RSS**, primer fotograma en
  **0,36 s** y salto al minuto 98 en **0,07 s**. El test lo comprueba sirviendo 300 MB enteros y midiendo el RSS del
  proceso.
  **C2/C3 · el camino bueno.** `GET /s/<sala>/file` sirve el original y `GET /s/<sala>/file.m3u` lo envuelve en un
  `.m3u` de una línea (doble clic lo abre en VLC o en mpv en los tres sistemas). La ruta real del fichero **no se
  publica**: `_media_public` ya quitaba `path` y ahora también `local`. Lo que no es obvio y hubo que resolver: mpv y
  VLC **no mandan la cookie de la sala**, así que el enlace lleva una credencial en la query (`?k=`). No es el token
  de invitación —con ese se entraría en la sala—: es el **mismo valor firmado de la cookie de ese invitado**
  (`Room.cookie_value`, HMAC con el secreto de la sala), que solo sirve para esto y caduca con la sala. Y como es por
  invitado, no puede ir en el aviso `media` que se reparte a todos: la página lo pide a `api/filelink`. El bloque de
  la página da las tres formas (copiar el enlace, bajar el `.m3u`, la línea `mpv "<enlace>"`) y la posición del
  anfitrión con un botón para copiarla: la sincronía de los pobres, que para ver una película con una llamada al lado
  sobra.
  **C4 · el camino se decide sin preguntar**, y «es un MP4, se verá» **no vale**: las grabaciones de esta casa son
  HEVC + Opus en MP4 **con el `moov` al final** (verificado con ffprobe), que es justo lo que el navegador lleva
  peor. Se comprueban los códecs, el contenedor y dónde está el `moov` (`hls.browser_playable` + `hls.moov_at_start`,
  que recorre los átomos de primer nivel). Dos trampas que salieron al escribirlo: (1) ffprobe llama
  `matroska,webm` **igual** a un `.webm` y a un `.mkv`, así que el contenedor no decide: un Matroska solo lo abre el
  navegador si por dentro es WebM de verdad (VP8/VP9/AV1 + Opus/Vorbis), y un `.mkv` con H.264 + AAC no va en Firefox
  ni en Safari; (2) con el `moov` al final el navegador tiene que bajarse la película entera antes del primer
  fotograma, mientras que mpv y VLC no se enteran porque saltan. Resultado: un archivo del anfitrión se ofrece
  **siempre** tal cual para «abrir en tu reproductor», y para el navegador va el original si puede con él y el relay
  de siempre si no. Lo que **no** se ha hecho: el remux `-c copy` a fMP4/WebM que midió el análisis (9,64 s para
  115 min). Con C5 la espera del relay deja de ser el problema, así que el remux pasa a ser una mejora de CPU, no un
  arreglo; queda apuntado en BACKLOG.
  **C5 · el relay empieza donde está el anfitrión.** `Input.start` añade `-ss` **antes** del `-i` (salto por el
  demuxer, no decodificando y tirando), el stream recuerda ese `offset` y la página lo descuenta: el reloj del vídeo
  que llega va `offset` segundos por detrás del reloj del anfitrión. Antes el invitado esperaba a que el empaquetado
  **alcanzara** la posición del anfitrión, y a 2,2× tiempo real con VA-API entrar en el minuto 40 eran **~18
  minutos** de «Preparando la retransmisión…». Ahora la página dice «Empezamos donde va el anfitrión (40:00)». No se
  pone `-ss` en un directo (TV, radio, un vídeo en vivo), donde la posición no quiere decir nada.
  **C7 · WebTorrent: no-objetivo**, con los números. Su única ventaja real es repartir la subida entre los invitados,
  y la subida medida aquí es de **~167 Mb/s** (22,7 y 19,1 MB/s con el endpoint `__up` de Cloudflare, dos pasadas de
  25 MB): caben **15 invitados** con una película 1080p buena (8,9 Mb/s) y **60** con una normal (2,2 Mb/s), y las
  salas de las que hablamos son de 2 a 5 personas. Resolvería un problema que no tenemos. Y lo que **no** resuelve:
  los **códecs** (pinta en la misma etiqueta `<video>`, así que sigue limitado a MP4/WebM, y lo que genera Ser es
  justo lo que el navegador lleva peor), la **sincronía** (un torrent no sabe de «vamos juntos»: habría que usar el
  SSE que ya tenemos, así que no ahorra código) y el **directo** (TV, radio o una grabación en curso no se pueden
  servir por torrent: seguirían necesitando el relay, con lo que sería un segundo camino, no un sustituto). Lo que
  cuesta: el anfitrión tiene que leerse la película entera para calcular el SHA-1 de las piezas antes del primer byte
  útil; cada invitado sube a los demás (en datos móviles, un gasto que no ha pedido, que es justo el caso del QR en el
  móvil); y WebRTC entre dos NAT necesita STUN y a veces **TURN**, o sea cambiar «un servidor mío» por «un servidor
  mío más un tracker más a veces un TURN». Se revisaría solo si (a) la subida medida bajara de ~20 Mb/s o (b) las
  salas pasaran de ~15 personas; y si algún día se hace, el camino correcto es el torrent normal con cualquier
  reproductor, no WebTorrent, porque WebTorrent hereda las limitaciones del navegador sin quitar ninguna.
- ADR-083 · Un solo menú: fuera el árbol de comentarios de `input.conf` (H46). **Sustituye** la parte de ADR-016 que
  decía «`ctrl+m` conserva el menú completo que uosc genera de input.conf» y la de ADR-018 que hacía la paleta leer
  los títulos de esos comentarios; el resto de los dos sigue en pie. **Qué había**: los comentarios `#!` de
  `input.conf` construían un **segundo menú**, el nativo de uosc (`ctrl+m`), con **119 entradas y 40 en el primer
  nivel**, peor que el nuestro y donde se colaban las duplicaciones: Biblioteca, Música, Audiolibros y Saltar intro
  aparecían **dos veces**, una como entrada suelta y otra como submenú. **Qué se hace**: `input.conf` se queda solo
  para las teclas (78, cero marcas de menú), desaparece la tecla que abría el otro menú y el único es `mu-menu`
  (tecla MENU, clic derecho, `alt+m` y el botón ▦), con ocho categorías en la raíz. Dos cosas que hubo que resolver
  antes de retirarlo, y que son el motivo de que esto no fuera un simple borrado:
  (1) **43 de esas líneas no tenían tecla**: existían solo para poner una entrada en aquel menú. Se revisaron una a
  una contra los menús de cada módulo; todas estaban ya cubiertas **salvo dos**, *Repetir la lista* y *Orden
  aleatorio de la lista*, que se han traído a «Herramientas» y a la paleta. Si no, se habrían perdido.
  (2) **La paleta (`alt+p`) leía los títulos de esos mismos comentarios** (`input-bindings` → campo `comment` →
  `^!`), así que borrarlos la habría dejado con los 26 comandos de su lista curada en vez de 93. Los 67 títulos que
  faltaban se han pasado a `CURATED`, en español y en un solo sitio, y la **tecla** se sigue leyendo del reproductor
  (`input-bindings` por comando) en vez de escribirse en la tabla, para que no se quede vieja cuando cambie
  `input.conf`. Tres tests nuevos lo sostienen: `input.conf` no puede volver a llevar marcas de menú ni líneas sin
  tecla, ninguna vista de `mu-menu` puede repetir un título (con la raíz de ocho o menos), y **ninguna tecla puede
  quedarse sin nombre** en la paleta o en algún menú, que era justo lo fácil de olvidar.
- ADR-084 · El nombre es «Atalaya Player», y qué se renombra y qué no (H41). Ser lo decidió el 2026-10-02 entre
  «Atalaya Player» y «Vanguardia Player». Razones, por si alguien las busca: una atalaya es *el sitio desde el que se
  mira y se ve venir lo que llega*, que describe lo que hace el programa (y engancha con el resumen y el índice, que
  no son solo ver sino tener perspectiva de lo que ves), mientras «vanguardia» es una afirmación sobre uno mismo que
  envejece. Y sobre todo: *La Vanguardia* es un diario de tirada nacional **que publica vídeo todos los días**, o sea
  colisión dentro de nuestra propia categoría, mientras «atalaya» choca con pueblos y con una minera, que no compiten
  con un reproductor. Comprobado el 2026-10-02: no existe ningún reproductor llamado así. Se conserva el sufijo
  «Player» porque, al contrario de lo que yo recomendé primero, **ayuda**: «Atalaya» a secas compite con los topónimos
  y «Atalaya Player» es un par casi único, que es justo lo que Ser quería (que la aplicación se encuentre).
  **Qué se renombra y qué no**, que es la parte con criterio: `brand.json` tiene tres campos y **no** tienen que
  coincidir. `name` = «Atalaya Player» es lo que se ve (menús, OSD, MPRIS, la tele por DLNA, las páginas de la sala y
  del mando, que ya sustituían el nombre solas). `folder` = «Atalaya» es donde caen los ficheros, sin espacio porque
  es una ruta, y de ahí sale también el nombre del AppImage (`Atalaya-x86_64.AppImage`): un fichero con un espacio en
  el nombre es un estorbo en cualquier terminal. `id` se queda en **`mpv-uos` a propósito**: es el socket, la carpeta
  de datos (`~/.local/share/mpv-uos`, con el historial, las preferencias, el índice de la biblioteca y las claves),
  la orden instalada, el `wayland-app-id` y el slug de Notion; renombrarlo obligaría a migrar los datos del usuario y
  a reinstalar sin ganar nada visible. Para quien quiera teclear el nombre nuevo, `tools/install.sh` instala un
  **segundo nombre**, `atalaya`, enlazado al lanzador (derivado de `folder` en minúsculas, así que sigue a la marca).
  Tampoco se toca el marcador `# generated by MPV-UOS tools/install.sh`: es con lo que el instalador reconoce lo que
  instaló él, y cambiarlo dejaría una instalación anterior sin poder actualizarse ni desinstalarse.
  **Lo que había que arreglar antes de poder renombrar**: «MPV-UOS» estaba literal en **30 ficheros de test**, la
  mayoría en las migas de los menús (`wait_nav(h, …, "MPV-UOS › TV y radio")`). Ahora el nombre y la carpeta salen de
  `brand.json` a través de `tests/conftest.py` (`APP`, `APP_FOLDER`, `APP_ID`), así que el próximo cambio de nombre no
  vuelve a tocar los tests. Lo que **no** se cambió: el texto de los subtítulos de prueba («Bienvenido a MPV-UOS…»),
  porque `tools/make_test_media.sh` genera voz que lo dice y cambiarlo rompería los tests de reconocimiento; los ADR y
  el histórico de `PROGRESS.md`, que son registro con fecha; y la carpeta del repositorio.
  Y la dirección del proyecto, **<https://solucionesconscientes.es/atalaya>**, es identidad igual que el nombre: vive
  en `brand.json` (`site`), la leen mpvd y los scripts, y se ve en *Ayuda* y en *Preferencias* (Enter la abre en el
  navegador, Tab la copia para llevársela al móvil).
- ADR-085 · La lista de reproducción se enseña al abrir varios archivos, y se quita sola (H47). Abrir varios de golpe
  **ya** construía la lista —lo hace mpv, y el `.desktop` pasa `%U`, comprobado: tres ficheros mezclados de vídeo y
  audio dan `playlist-count 3`—, pero no se veía: lo único que lo insinuaba eran los botones ⏮⏭ de la barra, que uosc
  solo pinta cuando hay lista (`<has_playlist>prev` en `uosc.conf`). Ahora, al cargar el primero de una lista de más
  de uno, se abre la lista de uosc y **se cierra sola a los 4 s** (`mu-menu-playlist_on_open`; 0 la desactiva y un
  número grande la deja hasta que la cierres). Se descartó dejarla puesta hasta que el espectador la cierre, que es
  lo primero que se pidió: le das a reproducir y lo primero que ves es un menú tapando la película. Y se descartó un
  simple aviso en el OSD, que no deja ver *qué* has abierto ni en qué orden. Guardas: no sale con un solo archivo, ni
  al cambiar de pista dentro de la lista (`playlist-pos` tiene que ser 0), ni si ya hay otro menú abierto, y al
  cerrarse comprueba que sigue siendo la lista y no otra cosa que haya abierto el espectador mientras.
- ADR-086 · No hay modo ligero: el consumo está medido y no hay nada que arreglar (H48). Ser preguntó si el
  reproductor podía consumir **menos que mpv pelado**. No: esto *es* mpv más 22 scripts Lua, uosc y un demonio en
  Python, así que el suelo es mpv pelado. Medido en este portátil el 2026-10-02 (muestras de 30 s, mismo fichero,
  `--vo=null --ao=null --hwdec=no`, CPU en porcentaje **de un núcleo** de cuatro): mpv pelado **17,9 %** / 84 MB;
  nuestro `mpv.conf` sin ningún script **14,1 %** / 73 MB; con **solo uosc** **13,7 %** / 75 MB; con **solo nuestros
  22 scripts** **22,7 %** / 92 MB; completo **24,1 %** / 97 MB. En pausa: pelado 0,0 % / 80 MB, completo **0,1 %** /
  91 MB, y **mpvd en reposo 0,00 % de CPU** y 59 MB. Conclusiones: **uosc es gratis** mientras reproduce (era la
  sospecha principal y queda descartada), **nuestro `mpv.conf` es más barato que el mpv de serie** (devuelve unos 4
  puntos), el coste real son nuestros scripts (+8,6 puntos y +19 MB) y el total frente a mpv pelado es **+6 puntos de
  un núcleo (1,5 % de la CPU de la máquina) y +13 MB**. Ningún temporizador desbocado: los rápidos (20 Hz en música,
  0,25 s en las cuentas atrás) se crean solo cuando hacen falta, el de música se autorregula a 4 Hz y los de
  `mu-intro` se crean y se matan en el acto; y solo hay 2 observadores de `time-pos` en 22 scripts.
  **Decisión: no se hace un modo ligero ni una configuración mínima paralela.** Lo que se ganaría es RAM, no fluidez
  (los scripts no están en el camino del vídeo y `hwdec=vaapi` ya está puesto), y costaría una segunda configuración
  que mantener, el doble de tests en lo que toque, y un tercer «modo» al lado del modo sencillo y el modo salón, en
  contra del criterio de H46 (una función, una puerta). Si algún día molesta, el camino es bisecar cuál de los 22
  scripts se lleva esos 9 puntos y arreglar **ese**, que aprovecha también cuando se usan las funciones.
  Aviso de método para quien repita la medida: con muestras de **15 s** los números bailaban entre 17 % y 35 %; hacen
  falta 30 s. Y el fichero de prueba es de 640×360, así que decodificar sale barato y el coste de los scripts parece
  proporcionalmente mayor de lo que será con una película de verdad.
- ADR-087 · Idiomas: la cadena castellana es la clave, y un solo sitio decide el idioma (H49). Ser pidió castellano,
  inglés y francés, con el idioma del sistema decidiéndolo (es/fr → ese; inglés o cualquier otro → inglés). Medido
  antes de diseñar: **~1.800 cadenas visibles** (1.151 en los scripts Lua, ~520 en mpvd, ~56 en el JS de las páginas
  servidas), o sea ~3.600 traducidas. El diseño entero está en **docs/IDIOMAS.md**; aquí lo que se decidió y por qué.
  **(1) La cadena en castellano es la clave**: `tr('Abrir o descargar')`, no `menu.open.title`. Es lo que hace viable
  migrar 1.800 cadenas: el código sigue legible (se ve el texto donde está), el castellano no necesita catálogo
  porque es la identidad, la extracción es mecánica y —lo importante— si alguien cambia el texto castellano y olvida
  el catálogo, la cadena **cae al castellano**, nunca a una clave cruda ni a un hueco en blanco. **(2) Un solo sitio
  decide**: `bin/mpv-uos` resuelve el idioma del entorno (`LC_ALL`, `LC_MESSAGES`, `LANG`, en el orden de POSIX;
  mpv no expone ninguna propiedad de idioma, comprobado) y lo pasa a los dos lados antes de que cargue el primer
  script, para que la pantalla de inicio no salga en un idioma y cambie al siguiente. **(3) uosc ya está traducido**
  (`intl/es.json`, `intl/fr.json` y más): sus menús y mensajes no se tocan, solo se le dice el idioma con su opción
  `languages`. Aprovecharlo en vez de duplicarlo. **(4) Las páginas servidas siguen al navegador del invitado**
  (`Accept-Language`), no al sistema del anfitrión: las abre otra persona y puede estar en otro idioma. Es lo
  contrario de lo que haría el camino fácil. **(5) No se traduce** lo que leemos nosotros —documentación, ADR,
  commits, comentarios— ni el texto de los medios de prueba, que la voz generada dice literalmente. La regla de
  `CLAUDE.md` sobre idiomas no cambia: sigue siendo código y comentarios en inglés, documentación y commits en
  castellano; lo que se traduce es lo que ve quien usa el programa. Y una preferencia permite forzar el idioma,
  porque alguien con el sistema en inglés puede querer el reproductor en castellano.

- ADR-088 · Entrar en una sala desde el reproductor, con el fichero original y la cuenta de la página (H44/C6).
  **Completa** ADR-082, que dejó servido el fichero original pero sin nadie que lo pidiera desde el propio
  reproductor. **Qué había**: `mu-share` solo sabía ser **anfitrión**. Para ver juntos, el invitado abría la sala en
  el navegador y ahí lo que llega es el relay: H.264 recodificado, saltos lentos y la CPU del anfitrión trabajando.
  La vía «abrir en tu reproductor» existía (C2/C3), pero era copiar un enlace a mano y pegarlo en mpv: el resultado
  era un vídeo suelto, sin pausas compartidas ni saltos. **Qué se hace**, y las tres decisiones que lleva dentro:
  **(1) El trabajo va a mpvd, no a Lua.** Entrar, mantener abierto el canal de eventos y corregir la deriva son
  cosas de red y de reloj; el hilo de Lua no puede bloquearse y no tiene con qué hablar HTTP. `mpvd/share/guest.py`
  hace el `api/join`, abre el SSE y manda `seek`, `pause` y `speed` al mpv que pidió entrar, por el IPC que ya
  existía. El script Lua solo pide el enlace, enseña el estado y sale. **(2) El invitado reproduce el ORIGINAL**
  (`/s/<sala>/file?k=…`), no el relay que recibe el navegador: es la razón de ser de esto. Calidad original, todos
  los códecs, saltos instantáneos y cero CPU del anfitrión. El relay solo se usa cuando no hay otra cosa (un
  directo). **(3) La corrección de deriva es la MISMA cuenta que hace la página**, portada constante a constante de
  `www/sync.js`, con un test que ejecuta las dos y compara: dos implementaciones separándose serían dos experiencias
  distintas en la misma sala, y es justo el tipo de diferencia que nadie ve hasta que alguien dice «yo lo tengo
  cuatro segundos por delante». Un salto grande (>1,5 s) se corrige saltando; uno pequeño (>0,15 s), reproduciendo
  hasta un 8 % más rápido o más lento, que no se nota. De paso, dos cosas que hacían falta: las rutas `media/` y
  `subs/` aceptan ahora la credencial en la query (`?k=`) igual que `file`, porque **un reproductor de verdad no
  manda cookies** y sin eso un directo no se podía seguir; y el aviso de «esa sala es tuya» se afina para que
  bloquee solo al anfitrión consigo mismo, no a **otro** reproductor del mismo equipo —que es exactamente cómo se
  prueba esto en casa, y cómo lo usará quien tenga dos pantallas. El enlace entra por los tres sitios por los que
  puede llegar: el lanzador (`bin/mpv-uos <enlace>` abre el reproductor ya dentro, y no se lo pasa a mpv, que se lo
  daría a yt-dlp para descargar una página web), la puerta única de H42 (pegarlo en *Abrir o descargar* ofrece
  «Entrar en esa sala» en vez de reproducir o descargar) y *Compartir → Entrar en una sala de otro…*.

- ADR-089 · Compartir: la sala sale a internet por defecto, el enlace se copia solo y no se entrega hasta que
  funciona (H51). **Sustituye** la parte de ADR-068 que decía «apagado por defecto» y la de ADR-030 que hacía del QR
  lo primero que se ve al crear una sala; el resto de las dos sigue en pie. De la prueba de Ser salieron cuatro
  quejas sobre compartir, y **tres tenían la misma causa**. **(1) El minuto de espera no era lentitud, era un enlace
  muerto**: medido tres veces el 2026-10-02, `cloudflared` imprime la dirección pública a los 5,6-7,7 s pero **esa
  dirección no enruta hasta 65-69 s**. Nosotros la dábamos por buena en el primer momento, así que lo que se copiaba
  —y lo que el invitado abría— no existía todavía. Ahora la sala se abre **al instante** con su dirección local, el
  túnel se abre **detrás** (antes `create` esperaba a cloudflared, y por eso tampoco aparecía el QR hasta pasados
  esos segundos: mismo origen), y la dirección pública **se sondea hasta que contesta**; solo entonces se da por
  buena, se copia y se avisa. Si no llega a contestar en cuatro minutos, se dice y la sala sigue sirviendo en la red
  local. **(2) El enlace, no el QR.** Una sala es para ver algo con quien **no está delante**, así que lo que hace
  falta es un enlace que pegar en un mensaje: se copia solo al crearla y se dice «Enlace copiado». El QR pasa a ser
  una fila del menú y `alt+Q`, para el caso real en que sirve (alguien con el móvil, aquí al lado). Y por lo mismo,
  **internet deja de ser opcional por defecto**: una sala que solo vale dentro de casa no hace lo que se le pide. La
  contrapartida es explícita: `tools/vendor.sh` instala ya `cloudflared` (`MU_VENDOR_CLOUDFLARED=0` lo deja fuera),
  porque un ajuste encendido por defecto que no puede cumplirse es peor que no tenerlo. **(3) «En el invitado no
  cambia la película»: dos fallos distintos.** La dirección del archivo original era `/s/<sala>/file` —**la misma
  cadena para todas las películas**—, así que ni el navegador ni VLC ni el modo invitado tenían forma de saber que
  había otra cosa; ahora lleva un testigo derivado del fichero y del secreto de la sala, que cambia con la película
  y no publica nada de la ruta. Y en la página, `useRelay` se ponía a `true` y **no volvía nunca**: tras una
  película que el navegador no abre, la siguiente que sí abría buscaba un relay que ya no existía, se quedaba sin
  dirección y el invitado seguía viendo la anterior **para siempre**. **(4) «Solo desde el navegador, no desde VLC o
  mpv»**: el bloque *Abrir en mi reproductor* solo existía cuando se compartía un archivo del anfitrión, así que con
  la TV o un vídeo de internet no había nada que llevarse. Ahora hay uno para cada caso, y para la retransmisión
  hubo que hacer algo que faltaba: **la lista HLS se reescribe con la credencial en cada trozo**, porque los nombra
  en relativo y un reproductor los habría pedido a pelo llevándose un 401 en el primero.

- ADR-090 · La barra y la línea de tiempo: tramos, bucle y notas donde se pulsan (H52). **Amplía** ADR-015 (la
  barra reducida de uosc) y **sustituye** la parte de ADR-016 que ponía «solo audio» entre los botones de pista.
  Ser pidió iconos en la barra para cortar y para repetir, poder elegir varios trozos y guardarlos sueltos o
  unidos, notas en la línea de tiempo, y una opinión sobre qué más poner y cómo simplificar. El diseño entero y su
  razonamiento están en `docs/INTERFAZ.md`; aquí van las decisiones y lo que las obliga.
  **(1) `chapter-list` pasa a tener un solo dueño, `mu-marks`.** Lo que uosc dibuja en la línea de tiempo son los
  capítulos de mpv, y esa propiedad **ya la escribía `mu-subs`** (capítulos por tema). Como cada script de mpv corre
  en su propio estado de Lua, dos que la escriban se pisan sin enterarse: por eso los tramos y las notas no la
  tocan, se la piden a `mu-marks`, que guarda los capítulos de verdad de la película, los mezcla con las marcas y
  los vuelve a poner al quitarlas o al cambiar de archivo. Efecto lateral aceptado: las teclas de capítulo también
  saltan de nota en nota y de tramo en tramo.
  **(2) La barra no se puede rehacer en caliente.** Comprobado en la uosc vendorizada: `Controls:init_options()`
  solo corre en `init()` y nada escucha los cambios de opciones, así que un «modo edición» que cambiara la barra
  entera exigiría parchear uosc. Se descarta; lo que hace ese papel es `hide` botón a botón, que su API sí admite,
  junto con `active`, `badge` y `tooltip`.
  **(3) Qué entra y qué sale.** Entran bucle, tramos y nota, porque pasan las tres preguntas que ahora quedan
  escritas: se pulsan a media película, dicen algo de un vistazo (estado o número) y se esconden cuando no aplican.
  Sale «solo audio»: es una decisión que se toma una vez por vídeo, solo aplica a vídeos de internet, y su icono se
  lee como «cámara apagada». Se queda en `alt+a` y en el menú. La barra además se **agrupa por significado**
  (mover · lo que ves · ritmo · lo que haces con ello · salir), que es lo que no estaba: «grabar» vivía pegado al
  menú y «solo audio» entre los botones de pista.
  **(4) Elegir una vez, decidir después.** El bucle y los tramos comparten la elección: mientras marcas, el
  principio y el final son `ab-loop-a`/`ab-loop-b` de mpv, así que uosc ya dibuja A y B sin ayuda; cerrado el
  tramo, se decide si se repite o se guarda. Es la misma pregunta («qué trozo») contestada una sola vez.
  **(5) Unir tramos se hace en una sola pasada de ffmpeg** (`trim`/`atrim` + `concat`), dentro del convertidor de
  siempre: así hereda cola, progreso, nombres, respaldo por CPU e historial, y solo hubo que añadir `ranges` al
  spec. Tiene un precio que se dice en voz alta: un filtro **obliga a recodificar**, de ahí que vaya por CPU (meter
  VA-API dentro de un `filter_complex` por tramo es pedirle problemas al controlador para ganar en un caso raro) y
  que los subtítulos incrustados se queden fuera. Guardarlos por separado no paga nada de eso: es la conversión de
  siempre con `-ss`/`-t`, y además ya nombraba los archivos `<película> [inicio-fin].ext` y desduplicaba.
  **(6) Las teclas no roban ninguna de mpv.** `ctrl+x` (el «cortar» de todo el mundo) marca, `ctrl+l` abre la
  lista, `n` anota y `l` pasa de `ab-loop` a repetir el tramo elegido, que es estrictamente más. Se descartó `x`
  porque mpv la trae puesta para el retardo de subtítulos.

- ADR-091 · La sala sigue los saltos, un enlace que sí abre VLC, y elegir qué tramos se exportan (H54).
  **Completa** ADR-082/C5 y ADR-089, y **amplía** ADR-090. De la segunda prueba de Ser salieron tres cosas.
  **(1) Un salto hacia atrás dejaba al invitado congelado.** Es la cara mala de C5: la retransmisión empieza
  **donde está el anfitrión** y solo contiene desde ahí, así que al saltar a un minuto anterior ese minuto **no
  existe** en lo que el invitado está viendo; la corrección de deriva lo empujaba una y otra vez al segundo 0 de la
  retransmisión, que se ve exactamente como lo describió Ser: «se queda en el mismo minuto, y tampoco tiene
  play/pause» —el vídeo no estaba roto, estaba siendo reposicionado veinte veces por segundo—. Ahora, cuando el
  salto cae fuera de lo que hay (antes del principio, o más allá de lo producido), **la retransmisión se rehace en
  esa posición**, con tres segundos de gracia para que varios tirones seguidos cuenten como uno. Y de paso: una
  retransmisión **no se anuncia hasta que su lista existe**, porque al rehacerla el invitado reengancha al instante
  y se llevaba un 404; en el primer arranque eso lo tapaba el «preparando».
  **(2) El enlace de la sala no puede abrir VLC, y no tiene arreglo.** Su contraseña va en el **fragmento**
  (`#k=…`) y un navegador no manda jamás el fragmento al servidor —que es precisamente lo que la mantiene fuera de
  los registros del servidor—, así que un reproductor que abra ese enlace no tiene con qué identificarse.
  Comprobado que el enlace que sí damos **lo abre mpv de verdad** (test con un mpv real, no con un cliente HTTP de
  mentira). Lo que faltaba era que el anfitrión lo tuviera a mano: *Compartir → Copiar el enlace para VLC o mpv*
  (`share.player_link`), que crea una credencial llamada «Reproductor» y la reutiliza mientras viva la sala en vez
  de llenarla de invitados de pega.
  **(3) Los tramos se eligen uno a uno.** Guardar era todo o nada. Ahora cada tramo entra marcado y Enter sobre su
  fila lo deja fuera; lo que se guarda es lo marcado, y *unir* solo se ofrece cuando hay más de uno elegido, porque
  unir uno no es unir nada. Las acciones por tramo (ir, repetir, quitar) pasan a los botones de la fila, que es lo
  que libera el Enter para lo que más se usa.

- ADR-092 · Control para los invitados, los dos enlaces de una vez, tramos ordenables y dos formatos más (H55).
  **Sustituye** la parte de ADR-030 que hacía entrar a todo invitado en «solo ver», y amplía ADR-089 y ADR-091.
  **(1) En una sala privada se entra pudiendo controlar.** Ser decía que desde el navegador «sigo sin poder
  controlarlo correctamente, lo único que funciona bien es silenciar» —y silenciar es lo único que NO necesita
  permiso—. Se comprobó con un navegador de verdad conduciendo pausa, −10 s y la barra: **los tres funcionan**. Lo
  que fallaba era el permiso: había que pedirlo y que el anfitrión lo concediera en un sí/no que se cierra solo a
  los 30 s. A una sala privada entra quien tú has invitado, así que ahora entra con el control; el interruptor
  *Los invitados pueden controlar* lo quita, y entonces la página **dice por qué** los botones están apagados, en
  vez de dejarlos muertos. En una sala pública sigue sin poder controlarse nadie.
  **(2) Los dos enlaces en un solo pegado.** El de VLC «no se dejaba copiar» porque se pedía justo al crear la
  sala, cuando todavía no hay nada que compartir y la llamada falla. Ahora la invitación se entrega **cuando la
  sala sirve de verdad** (el túnel contesta y hay medio preparado) y lleva los dos enlaces con una línea sobre qué
  hace cada uno. Un solo pegado, porque el portapapeles es uno y acordarse de mandar dos mensajes no es cosa de
  quien comparte.
  **(3) Los tramos se ordenan.** Icono propio en la barra con el número (estaban solo detrás de una tecla y del
  menú, y «es difícil encontrarlo»), flechas por fila para subir y bajar, y **unir respeta ese orden**: hubo que
  quitar de la validación la exigencia de que los tramos fueran en orden y sin solaparse, que era mía y sobraba —
  `trim`+`concat` pega lo que le den en el orden que le den, y poder montar el final primero es justo lo pedido.
  **(4) Dos formatos más, y uno importa de verdad.** «Sin recodificar» copia los flujos tal cual: **0,07 s para un
  corte de 6 s** y sin perder un bit, frente a recodificarlo entero; el precio, dicho en la propia etiqueta, es que
  empieza en el fotograma clave anterior y que **no puede unir** (pegar obliga a recodificar). Y **AV1** por
  SVT-AV1 para quien quiera el mínimo tamaño. La lista de formatos pasa a salir de mpvd, que **quita los que esta
  máquina no puede hacer**: ofrecer un formato imposible es peor que no ofrecerlo. Nota sobre **grabar**, que Ser
  preguntaba: ahí no hay códec que elegir y no es una carencia — grabar **copia el flujo tal cual**, así que el
  códec es el que venga por la antena o por la red, y lo único elegible es el envase (MKV acepta todo, MP4 solo
  algunos). Es lo que permite grabar sin gastar CPU y sin perder calidad en un portátil de cuatro núcleos.

- ADR-093 · De la tercera prueba: lo que estaba roto era mío, y lo que «no hacía nada» no se veía (H56).
  **Corrige** ADR-092, cuya tanda se dio por buena antes de que terminara su batería de pruebas: aquella pasada
  acabó con **once fallos** y se publicó igualmente. La lección está anotada en PROGRESS y vale más que el ADR: una
  tanda no está hecha hasta que `check.sh` ha terminado, y contestar antes es contestar sobre algo que no se sabe.
  **(1) El invitado fantasma.** Para firmar el enlace de VLC se creaba un invitado llamado «Reproductor»… que
  aparecía en la lista, ocupaba plaza y se contaba como espectador, y en una sala pública con tope de visitantes
  rompía la entrada de los demás. Ahora es `hidden`: existe para firmar y para nada más.
  **(2) «Guardar los tramos no hace nada».** Hacía: los archivos se creaban. Lo que no había era **señal** —iban a
  una carpeta que nadie ha visto nunca y, al unir, se recodificaba en silencio—. Ahora se dice a dónde van y hay
  una fila para ver cómo van. Y había un caso en que de verdad no hacía nada: **con la TV o un vídeo de internet**,
  que no son archivos de este equipo; eso se avisaba con un OSD de un segundo *después* de pulsar, y ahora se dice
  **en el menú, antes**, con el camino que sí sirve (Grabar).
  **(3) La TV pedía darle al play.** `pause` es una propiedad **global** de mpv y sobrevive al cambio de archivo:
  si venías de pausar algo, el canal entraba pausado. Al elegir un canal lo que quieres es verlo.
  **(4) Tests que encodaban contratos viejos.** Media docena asumían que un invitado entra en «solo ver»; se han
  adaptado conservando el camino de pedir/conceder el control, que sigue existiendo cuando el anfitrión se los
  queda. Y uno fallaba **según la hora del día**: la guía de TV intercala una fila de día cuando el programa
  siguiente cae pasada medianoche, así que a partir de cierta hora el índice bailaba.

- ADR-094 · Una franja programada puede grabar, poner o las dos cosas (H57). **Amplía** ADR de H21 y H40, que
  dieron por supuesto que programar una hora era programar **una grabación**. Ser pidió lo contrario: «a X hora se
  enciende el canal de TV o radio, finaliza la emisión a la hora deseada y si es preciso suspensión o apagado», y
  lo mismo con canciones o listas. **Lo importante es lo que NO se hizo**: no hay un sistema nuevo de alarmas. El
  programador de grabaciones ya tenía franja horaria, despertador por RTC, los tres seguros de apagado y una lista
  con su menú; lo único que le faltaba era un campo `mode` (`record` | `play` | `both`). Dos decisiones dentro:
  **(1) Si no hay reproductor abierto, mpvd abre uno.** Sin esto la función no sirve para lo que se pide: lo que da
  sentido a «a las 7:00 que suene la radio» es justamente que el equipo esté suspendido, que el despertador lo
  levante y que no haya ninguna ventana abierta. mpvd conoce la raíz del proyecto, así que lanza `bin/mpv-uos` y
  espera a que la sesión se registre, con un tope; si no llega, la programación se marca como fallida y dice por
  qué, en vez de quedarse en silencio.
  **(2) Una canción o una lista se envuelven como «canal».** `media_channel()` construye un canal de pega con una
  URL o una ruta, de modo que la música hereda sin tocar nada el despertador, la franja, el apagado al terminar, la
  lista de programaciones y su menú. La alternativa —un segundo programador para medios locales— habría duplicado
  lo más delicado del programa (el que decide apagar el equipo) para no ganar nada.

- ADR-095 · Que se vea lo que pasa, avisar antes de pulsar, ir a un minuto, y el vídeo acortado (H58).
  Las tres primeras salen de la prueba de Ser; la cuarta es la idea de `docs/IDEAS.md` 2.1, que eligió hacer.
  **(1) Un indicador de trabajo en la barra.** La queja «guardar los tramos no hace nada» era **falsa** —los
  archivos se creaban— pero no había **ninguna señal**: iban a una carpeta que nadie había visto y, al unir, se
  recodificaba en silencio. Desde fuera eso es idéntico a estar roto. `mu-convert` ya recibía los eventos de
  tareas, así que el indicador no cuesta ni una consulta: icono con el número mientras hay trabajo, con el nombre
  y el porcentaje de la que corre, y `hide` cuando no hay nada. Es el uso que la API de uosc pedía desde H52.
  **(2) Una opción que no puede funcionar no se ofrece como si pudiera.** Queda escrito en `docs/INTERFAZ.md`:
  apagada, diciendo por qué y señalando lo que sí sirve, **antes** de pulsar y no después.
  **(3) Ir a un minuto escribiéndolo.** Admite `2:15`, `1:02:15`, segundos sueltos y relativos (`+30`, `−30`),
  porque es lo que se escribe de verdad. La tecla es `g`, que mpv trae como `ignore`: no se le roba ninguna.
  **(4) El vídeo acortado, y aquí está la decisión que lo hace posible.** Montar los trozos elegidos **no genera
  ningún archivo**: se usa la línea de tiempo virtual de mpv (`edl://`), que los reproduce seguidos sin recodificar
  y se abre al instante. Guardar un archivo sigue estando, pero es otra cosa y cuesta, así que es un paso aparte.
  Dos detalles que solo aparecen al probarlo: una ruta con una **coma** rompe el montaje («EDL parsing failed»,
  comprobado) y hay que escaparla con `%<bytes>%<ruta>`, cosa nada rara en nombres de película; y la elección de
  tramos **tuvo que rehacerse** — puntuar frases sueltas daba setenta y cinco trocitos de catorce segundos, que es
  un tartamudeo y no un montaje, así que la puntuación se suaviza entre frases vecinas para que ganen **pasajes**,
  y se ajusta por búsqueda binaria cuántas frases se cogen, porque fundir y estirar infla el total (quince minutos
  pedidos daban diecisiete). Con eso: 15 min pedidos → 14,9 reales, en tramos de 25 s a 2 min.

- ADR-096 · Quitar el vídeo al minimizar, y por qué el botón deja de hacer falta (H60).
  **Sustituye** la parte de ADR-058/H32 que dejaba este ajuste apagado, y retira el `prefer_audio` de H14.
  Ser preguntó si minimizar ya deja de decodificar y, si es así, si el botón de apagar el vídeo es redundante.
  **(1) Minimizar no para nada.** Medido dos veces con la ventana real y un 720p HEVC, cambiando `window-minimized`
  por IPC y muestreando `/proc/<pid>/stat`: 37,2 % y 32,6 % de un núcleo con la ventana visible, 17,9 % y 17,8 %
  minimizada, 11,7 % y 8,1 % sin vídeo, y los mismos fotogramas descartados (11) en los dos casos con imagen. Lo
  que ahorra minimizar es **pintar**; mpv sigue decodificando cada fotograma para nadie.
  **(2) Y no hace falta recargar para quitarlo, ni en internet.** Esto es lo que cambia el diseño. La idea inicial
  era activar `ytdl_hook-all_formats=yes` para que cada formato fuera una pista de carga diferida; midiéndolo
  resultó innecesario: con ytdl_hook **tal cual está**, deseleccionar la pista de vídeo de un vídeo de YouTube baja
  la descarga al 35 % (98,7 → 34,1 KiB/s en 480p), tarda 0,03 s, no corta el sonido y la posición sigue corriendo
  (27 → 52 → 77 s), porque el vídeo viene en su propio flujo y mpv deja de leerlo. Con lo que se cae todo el riesgo:
  no se toca la selección de formatos, ni se pierden etiquetas, ni hay que probar `all_formats` en directos.
  **(3) Encendido por defecto.** H32 dejó el ajuste apagado por prudencia; con 0,03 s de ida y vuelta no hay motivo.
  Vale igual para un fichero y para una URL: el manejador no excluía las direcciones, solo le faltaba estar puesto.
  **(4) Fuera `prefer_audio`.** Recordar «abre los vídeos de internet sin imagen» era la única cosa que minimizar no
  puede expresar, pero también la que confunde: abres un vídeo y no tiene imagen sin saber por qué. Los vídeos se
  abren **siempre** con su imagen; `alt+a` es cosa del momento y no recuerda nada. El ahorro máximo de datos —bajar
  solo la pista de audio— sigue existiendo donde se pide a propósito: «Solo audio» en el menú de calidad.
  **(5) El botón ya no estaba.** `mu-audio` salió de la barra en H51 al hacer sitio a tramos, bucle y notas; quedó
  escrito en `docs/INTERFAZ.md` pero no se le dijo a Ser, y el código seguía registrando en uosc un botón que nadie
  dibujaba. Se retira el registro. Y de paso el toggle deja de contar una reproducción nueva, porque ya no recarga.

- ADR-097 · El modo sencillo encoge la barra escondiendo botones, no reescribiendo la opción (H53).
  **Corrige** ADR-058, que dio por hecho que escribir `uosc-controls` bastaba. No bastaba: uosc lee esa opción en
  `Controls:init_options()`, que solo corre en `init()`, y ningún elemento escucha cambios de opciones, así que el
  menú encogía y la barra se quedaba idéntica. El test de entonces comprobaba que la opción se escribía, no que la
  barra cambiara, y por eso no saltó.
  **La decisión es dónde ponerlo.** El BACKLOG proponía que cada script escondiera su botón (`hide`, que la API
  pública de uosc sí admite en caliente). Se hace, pero en **un solo sitio**: el módulo `mu.uosc`, por donde pasan
  todos los `set-button`. Script a script habrían sido cuatro ficheros y un olvido garantizado en el siguiente botón
  que se añada; en el módulo lo heredan los veinte scripts y también los que aún no existen. Se conserva `mu-menu`
  —sin él el modo sencillo no tiene puerta— y se sigue escribiendo `controls` para el arranque siguiente, que es
  cuando uosc sí la lee: las dos cosas describen la misma barra.
  **Lo que no se puede**: esconder los elementos propios de uosc (play, anterior/siguiente, audio, velocidad,
  pantalla completa). Da igual, porque son exactamente los que el modo sencillo quiere conservar.
  **Y cómo se comprueba**: uosc no publica sus botones, así que `mu.uosc` publica en `user-data/mu/bar/<script>` qué
  tiene escondido. El test mira eso, no la opción. Que uosc obedezca a `hide` está verificado en su código
  (`ManagedButton.lua:27-32`) y ya lo usaba el indicador de tareas de H58.

- ADR-098 · Programar una lista guardada: `loadlist` y repetir hasta el final de la franja (J5).
  **Completa** ADR-094, que envolvió «una canción, una carpeta, una lista o una dirección» como canal de pega pero
  dejó dos cabos: desde el menú solo se podían elegir **canales**, y `_play` cargaba todo con `loadfile`.
  **(1) Las listas ya estaban.** `mpvd/music/playlists.py` guarda listas con nombre como M3U8 en los datos del
  usuario, con crear/renombrar/añadir/ordenar/importar/exportar, y `music.playlists.list` ya devuelve la ruta del
  fichero. Lo que había que construir no era el almacén, era el camino hasta él.
  **(2) `loadlist`, no `loadfile`.** Un `.m3u8` abierto con `loadfile` se intenta demuxear; mpv solo encola una
  lista con `loadlist`. Se distingue por el sufijo **y porque sea local**: un `.m3u8` remoto es HLS, o sea un canal,
  no una lista de canciones. Así no hay que añadir ningún campo al modelo de `Channel`.
  **(3) La lista se repite durante la franja.** «Música de 21:00 a 23:00» con una lista de veinte minutos se
  acabaría a y veinte, que no es lo que se ha pedido: se pone `loop-playlist=inf` mientras dura y se devuelve al
  terminar el valor que hubiera (es una opción global del usuario, no nuestra).
  **(4) Con algo del disco, el modo es «ponerlo».** Grabar un fichero que ya está en el disco no tiene sentido;
  mpvd ya lo forzaba y ahora el menú lo dice en el título («Poner «Cena»…» y no «Grabar»).
  **(5) De paso**: `views.sched_time` publicaba el nombre de la vista donde las demás publican el título, así que no
  se podía comprobar desde fuera qué se iba a hacer. Ahora publica el título, como el resto.

- ADR-099 · El remux para el navegador se descarta: medido, no mejora nada (H44/C8).
  **Cierra** el único cabo que quedaba de ADR-082/H44, y **corrige la nota del BACKLOG** que lo justificaba.
  La idea era «remux `-c copy` para el navegador cuando los códecs lo permitan, **en vez del relay recodificando**:
  9,64 s para 115 min frente a 2,2× tiempo real». La premisa estaba mal: el relay **ya** copia cuando los códecs lo
  permiten. Comprobado llamando a `plans_for` con un MKV de H.264 8-bit + AAC —el caso típico— devuelve modo `copy`
  con `-c:v copy -c:a copy`. No había recodificación de la que ahorrar.
  **Medido para el mismo fichero**: remux a MP4 con `+faststart` 0,08 s, relay HLS en copia 0,08 s; 1.690.840 bytes
  frente a 1.814.052, un 7 % menos por la cabecera de MPEG-TS. Nada que justifique tocar la parte del programa de
  la que más se ha quejado Ser.
  **Donde el relay sí recodifica** (HEVC + Opus en MP4, que es lo que graba esta casa, a 2,2× tiempo real) un remux
  **no sirve**: eso no lo abre ningún navegador por mucho que se reempaquete. Ese caso ya está resuelto por otro
  lado desde C6: al invitado se le ofrece su propio reproductor con el fichero original, sin recomprimir nada.
  **Lo único que daría un fichero completo** es poder saltar a cualquier punto de golpe, en vez de rehacer el relay
  cuando el salto cae fuera (H54). Pero cuesta una copia del tamaño de la película en la caché y hasta diez segundos
  antes de que el invitado pueda empezar, justo lo contrario de la queja de Ser («tarda un min aprox en cargar»);
  y un fMP4 que crece empieza rápido pero no salta mejor que ahora. Si el salto vuelve a dar problemas, la palanca
  barata es la reconstrucción del relay que ya existe.

- ADR-100 · Volumen parejo y temporizador: el trabajo era enseñarlos, no construirlos (K3).
  **Amplía** ADR-051 (el nivelador `dynaudnorm`) y la parte de audio de `mu-music`, sin cambiar ninguna de las dos.
  Al abrir K3 resultó que lo pedido ya estaba hecho: `mu-music` aplica el `replaygain` de mpv (por pista o por
  álbum) para lo que trae etiquetas y, para la música que no las trae, la ganancia que mide mpvd puesta como
  `replaygain-fallback`; y el temporizador de `mu-books` dice en su propia cabecera que vale para cualquier
  reproducción, no solo para un libro. Lo que fallaba era **dónde estaban**: el volumen, dentro de *Música*, que es
  el último sitio donde se mira viendo una película; el temporizador, dentro del menú de *Audiolibros*.
  **La decisión es no unificarlos en un interruptor «inteligente».** Lo tentador era una sola fila que eligiera por
  su cuenta: etiquetas donde las haya, filtro donde no. Se descarta: el filtro aparecería y desaparecería entre
  canciones según las etiquetas de cada fichero, y un sonido que cambia de carácter solo es desconcertante. En su
  lugar, las dos filas van **juntas** y cada una dice lo que cuesta —gratis pero solo donde hay etiquetas, o válida
  para todo a cambio de CPU—, que es información con la que se puede decidir.
  **Y una sola verdad**: la fila de *Imagen y sonido* no guarda nada propio, le manda el modo a `mu-music` por
  `mu-music-replaygain`, porque quien aplica la ganancia es él. Dos preferencias para lo mismo acaban siempre en
  que una miente.

- ADR-101 · Minimizar tiene que DECIRSE, y el sitio es el título de la ventana (H60, de la prueba de Ser).
  **Corrige** ADR-096, que encendió el ajuste y lo dejó **invisible**: `on_minimized` no avisaba de nada (cero
  `osd` en todo el manejador). Ser lo dijo en una frase —«cuando minimizo no sé si se sigue decodificando o no»—
  y tiene razón: es exactamente lo que ADR-095 escribió como regla para los demás y aquí no se aplicó.
  **El problema es dónde ponerlo.** Con la ventana escondida un OSD no sirve: se dibuja sobre un vídeo que nadie
  ve. Lo único que sí se ve de una ventana minimizada es su **título**, que es lo que enseña la barra de tareas, y
  `title` es una propiedad que mpv acepta cambiar en caliente (comprobado). Así que mientras está minimizada el
  título pasa a «🎧 Solo audio (minimizado) — …» y al restaurar se devuelve la plantilla que hubiera, que se guarda
  de `options/title` (la propiedad `title` da la plantilla sin expandir, no el texto final).
  **Y una vez, al volver, se cuenta lo que ha pasado** (`told_minimized`): sin eso nadie puede enterarse de que la
  función existe, y una función que ahorra algo sin decirlo es indistinguible de un fallo. Repetirlo cada vez sería
  ruido, así que es una sola vez en la vida de la instalación.
  **De paso se gana el test que faltaba**: el título es observable desde fuera, así que la prueba ya no depende de
  dormir y mirar la CPU — comprueba que el título cambia al minimizar y vuelve al restaurar.

- ADR-102 · Lo que pasa por detrás se ve y se dice, y el mando del televisor se lee del kernel (H62, H61).
  **Amplía** ADR-095, que puso el indicador de trabajo en la barra pero solo para conversiones y descargas.
  **(1) Guardar un tramo no recodifica.** El formato de fábrica era MP4, o sea recodificar, y Ser daba por supuesto
  lo contrario («por defecto no lo transcodifica, ¿no?»). Tenía razón en lo que esperaba: cortar es copiar los
  flujos (0,07 s frente a 1,16 s, y sin perder un bit). Unir varios en uno sí obliga, porque pegar trozos es un
  filtro de ffmpeg; eso se dice en la lista **antes** de pulsar y se señala dónde cambiarlo.
  **(2) «Tareas» enseña todo, no una parte.** Los subtítulos con IA, la traducción, el índice por temas, la intro o
  la música ya pasaban por la **misma cola de trabajos** del servidor: lo único que faltaba era publicarlos. Dos
  cuidados que son la decisión de verdad: el nombre interno (`asr.model.small`) **se traduce**, porque un panel que
  dice `semantic.index` no informa a nadie; y un trabajo sale **solo si es pesado o si lleva más de 2 s corriendo**,
  porque un indicador que parpadea con cada chapucilla de 50 ms —comprobar un canal, pulsar en una emisora— se
  aprende a ignorar, y entonces no sirve para nada. Una conversión no se cuenta dos veces: ya tiene su fila.
  **(3) Avisar al terminar es cosa del escritorio, no del OSD**, porque lo que hay que cubrir es justamente que no
  estés mirando. Solo por encima de 20 s (avisar de algo que acabó delante de ti es ruido) y siempre si ha fallado.
  **(4) Y el mando del televisor se lee del kernel, no de `cec-client`.** Esto cambió al construirlo: la idea era
  leer la salida de texto de `cec-client`, cuyo formato no se puede comprobar sin el programa instalado —y adivinar
  formatos es justo lo que este proyecto no hace—. El aparato del kernel (`/dev/cec0`, `CEC_S_MODE` para hacerse
  «follower» y `CEC_RECEIVE` para cada mensaje) es una interfaz binaria **documentada**, no necesita instalar nada
  en la Pi y tiene la misma forma que el joystick que ya lee el gamepad. El tamaño de `struct cec_msg` (56), el
  desplazamiento de `msg` (32) y los dos números de ioctl salen de `/usr/include/linux/cec.h` comprobados con
  ctypes. Una tecla se traduce a **las mismas acciones que ya manda el gamepad**, así que el reproductor no gana un
  segundo mapa. A diferencia del gamepad **manda siempre**, no solo en modo salón: es un mando físico, y uno que no
  hace nada al pulsarlo es el fallo silencioso que llevamos toda la semana quitando. Sin televisor aquí, se prueba
  dándole al demonio los mismos `struct cec_msg` por un FIFO, como el gamepad; la última milla, en la Pi.

- ADR-103 · Explorar las carpetas del equipo, y lo que NO se construye para ello (H64).
  Ser lo pidió pensando en la Raspberry conectada al televisor, y ese detalle manda en todo el diseño: lo único que
  había para elegir una carpeta era **teclear la ruta**, y en un salón no hay teclado.
  **(1) Lo que no se construye.** Reproducir una carpeta entera no lleva ni una línea de expansión: mpv 0.41 abre
  directorios él mismo (`--directory-mode`, `--directory-filter-types`, comprobado contra el mpv instalado), así
  que se le pasa la ruta. Tampoco se construye un índice: `files.browse` lista un nivel y se olvida. Para indexar
  ya está la biblioteca, que escanea en segundo plano; un navegador tiene que contestar al instante aunque la
  carpeta tenga diez mil archivos, y por eso solo cuenta un nivel al decir lo que hay dentro.
  **(2) Las puertas incluyen las unidades conectadas**, que es lo que de verdad se va a usar en la Pi: un pincho o
  un disco USB aparece solo (`/media/<usuario>`, `/run/media/<usuario>`, `/mnt`; `/Volumes` en macOS y las letras
  en Windows). Sin eso, «explorar» en una Pi no sirve para nada, porque las películas están en el disco de fuera.
  **(3) La forma la decide el mando, no el teclado.** Lo que se puede hacer con una carpeta —reproducirla entera,
  añadirla a la biblioteca— son **filas**, no acciones de Tab: el mando de un televisor no tiene Tab. Y hay una
  fila **«Subir»** porque en ese mando la tecla «atrás» está mapeada a cerrar el menú (ADR-102), no a subir un
  nivel; sin ella se podría entrar en una carpeta y no haber forma de volver.
  **(4) Orden natural y lo que estorba fuera.** Las subcarpetas primero y «Capítulo 2» antes que «Capítulo 10»,
  que es lo que espera cualquiera; los archivos que este reproductor no abre no se listan, y los ocultos tampoco,
  aunque las dos cosas se pueden pedir. Entrar en una carpeta es un marco más de la pila de vistas, así que ⌫
  sube solo, y un testigo de secuencia evita que una respuesta lenta pinte la carpeta de la que ya has salido.

- ADR-104 · Un menú que se ha cerrado no se reabre solo (H65).
  Lo encontró la batería completa, y es el mejor argumento a favor de pasarla: el síntoma parecía lentitud —un test
  esperando a que el menú se cerrase— y era un **fallo real de uso**: cierras el menú de TV mientras está cargando
  y vuelve a aparecer solo. Con treinta segundos de espera seguía abierto, así que no era lentitud de nadie.
  **La causa son dos cosas razonables que juntas se estorban.** Cada vista pinta dos veces (las filas de
  «cargando» al entrar y las de verdad cuando contesta mpvd), y `show()` abría el menú siempre que uosc no tuviera
  ya ese menú abierto — lo que es correcto para el primer pintado y desastroso para el segundo. Y el reinicio de la
  navegación está retrasado 0,2 s **a propósito**, porque uosc pasa por `nil` al sustituir un menú y sin ese
  retardo se perdería la pila al navegar; ese retardo es exactamente la ventana por la que se colaba la respuesta
  tardía.
  **La decisión: abrir hay que haberlo pedido.** `open_view` da un permiso y lo consume el primer pintado que
  abre; el permiso **caduca en el instante** en que no hay menú, sin esperar al retardo, porque si lo que viene es
  una sustitución el `open_view` de la vista nueva lo dará otra vez. Una respuesta que llega tarde, como mucho,
  actualiza un menú que siga abierto. Un detalle que costó un intento: el permiso **no** puede apagarse al volver
  de `open_view`, porque hay vistas que solo pintan desde su callback y entonces el primer pintado llega después
  —apagarlo allí dejaba el menú sin abrirse nunca—.
  **Y el detector ya estaba escrito**: el test que espera a que el menú se cierre. Fallaba con el fallo puesto y
  pasa con él quitado. Que un test «sensible a la carga» resulte ser un fallo de verdad es la razón por la que
  H63 dice que una batería que falla al azar se deja de leer.

- ADR-105 · La carrera de las filas viejas se arregla en los scripts, no en los tests (H63/N1).
  **Cierra** la clase de fallos que ADR-093 dejó anotada y que ha mordido cuatro veces en dos días.
  El síntoma era siempre el mismo: un test espera a que el estado diga «estoy en la vista X», lee `items` y se
  encuentra las filas de la vista anterior, porque el estado se publica **dos veces** (la vista al entrar, sus filas
  cuando llegan). La tentación era repasar las sesenta esperas y hacerlas esperar por una fila. Se descarta: eso
  arregla los tests de hoy y no impide los de mañana.
  **La decisión es vaciar las filas al cambiar de vista** (`open_view`), en los trece scripts que las publican. Con
  eso leer las filas de la vista anterior pasa de ser un error silencioso a ser **imposible**: lo peor que se puede
  leer es una lista vacía. No es una idea nueva: `mu-av` lo hacía desde que se escribió, con el comentario puesto,
  y nunca dio este problema — era aplicar lo que ya estaba demostrado.
  **Y el efecto buscado era que algo fallara.** Falló: cuatro tests que venían pasando leían filas viejas sin que
  nadie se enterase (`test_mu_convert`, `test_mu_feeds`, `test_mu_library`, `test_mu_music`). Dos de ellos, además,
  por una razón que conviene recordar: su helper hacía `next(...)` sin valor por defecto **dentro de un
  `wait_property`**, así que la excepción tumbaba la espera entera en lugar de volver a mirar un instante después.
  Un predicado no puede reventar; tiene que contestar que todavía no.

- ADR-106 · Ver un torrent mientras se descarga: lo que lo hace posible es el lector, no el cliente (H59).
  **NO VIGENTE desde el 2026-10-04: la sustituye ADR-111, que quita los torrents del programa.**
  **Reabre y sustituye** la decisión de H26 (2026-09-30), que descartó los torrents y proponía integrarse con
  qBittorrent por su interfaz web. Ser lo aprobó el 2026-10-03 con una condición que ordena todo lo demás: extra
  **opcional y apagado**, como los servicios de nube.
  **(1) La pieza de verdad es el lector, no el cliente.** Bajar un torrent lo hace libtorrent; lo que convierte
  «descargar» en «ver» es que mpv pida un rango por HTTP y que a las piezas de **ese** rango se les ponga
  `set_piece_deadline`. Se descarta `set_sequential_download`, que es lo que hace medio mundo: va bien hasta que
  alguien salta hacia delante, y entonces hay que esperar a que la descarga llegue allí. Con fechas límite por
  rango, un salto es otro rango y se pide igual; el test lo comprueba leyendo **el final del fichero** cuando
  todavía no está descargado.
  **(2) mpv no sabe nada de torrents**, y eso es deliberado: recibe `http://127.0.0.1:<puerto>/t/<id>/<n>?k=…` y
  pide rangos como a cualquier servidor. Así «ver mientras baja» no es un modo aparte con sus propios fallos: es el
  reproductor de siempre, con sus subtítulos, su resumen y su «ver juntos».
  **(3) Medido antes de prometer** (L10): 12,1 ms de CPU por MB y 110 MB de RSS, o sea un 6 % de un núcleo bajando
  a 5 MB/s. Eso es lo que permite decir que se puede ver una película mientras baja en un portátil de cuatro
  núcleos sin GPU, en vez de suponerlo.
  **(4) Lo que NO se toca**: los valores de libtorrent (DHT, LSD, cifrado, 200 conexiones) se quedan como vienen,
  porque los ha probado muchísima más gente que nosotros; la subida **no** se estrangula, porque un cliente que no
  devuelve nada es un cliente que no baja; y **no hay blocklist**, que bloquea rangos enteros por reputación, rompe
  conexiones legítimas y hay que mantenerla. Los trackers extra (20, en caché semanal con copia en el repo)
  **nunca** se añaden a un torrent privado: anunciarse fuera de su tracker es motivo de expulsión.
  **(5) Un error propio que conviene recordar**: la primera pasada del test escribió en `~/.config/mpv-uos/` —la
  configuración DE VERDAD de quien ejecuta la batería— y dejó los torrents encendidos. El resto de rutas ya estaban
  aisladas en los tests; esta era nueva y se me pasó. Ahora `MPV_UOS_CONFIG_DIR` también apunta a la carpeta del
  test, y el fichero que se creó se borró a mano.

- ADR-107 · Las páginas servidas se traducen con un `/i18n.js` propio, y el HTML se traduce en el navegador
  (H49/G6).
  La sala, el mando y el panel los abre **otra persona** en **su** navegador, así que el idioma lo decide su
  `Accept-Language` (ADR-087 ya lo dejó dicho; esto es cómo se hace).
  **(1) No se manda el catálogo entero.** Son 1.538 cadenas, unos 75 KB, en un móvil que quizá está en 3G y para
  una página que usa setenta. El servidor calcula las cadenas de esa carpeta (`page_keys`: los `t('…')` de sus
  `.js` y el texto de sus `.html`), se queda con las que tienen traducción y las sirve en `/i18n.js` —la sala, en
  `/static/i18n.js`— delante de la plantilla `mpvd/i18n_page.js`, que trae el `t()` y el paso que traduce el HTML.
  En castellano se sirve `window.MU_T = {}`, porque la página ya está escrita en castellano: el peor caso sigue
  siendo «se ve en español».
  **(2) Un fichero, no un `<script>` en línea.** La primera versión metía el catálogo en un hueco
  `{/*i18n*/}` dentro del HTML. Funcionaba en el mando y en el panel, y **no** en la sala: se sirve con
  `Content-Security-Policy: script-src 'self'`, que bloquea lo que va en línea. Y lo bloquea **callando**: no salta
  `window.onerror`, así que el síntoma era una sala que cargaba y se quedaba muda («t is not defined» dentro de un
  `catch`), con el vídeo sin empezar. Aflojar la CSP para una traducción habría sido cambiar la seguridad de la
  página —que se expone a internet por el túnel— por una comodidad; servir un `.js` más cuesta una petición que el
  navegador cachea igual.
  **(3) El HTML ya escrito se traduce en el navegador**, con un recorrido de los nodos de texto y de los atributos
  que una persona lee (`placeholder`, `title`, `aria-label`, `alt`), usando el mismo catálogo. La alternativa era
  marcar cada etiqueta con `data-i18n` —cien atributos a mano— o montar plantillas en el servidor. La clave es el
  texto castellano, apretando los espacios en blanco, así que la indentación del HTML no la cambia. El cambio de
  marca (`brand.json`) se aplica al catálogo entero, clave incluida, porque el HTML también se reescribe: las dos
  mitades tienen que decir lo mismo para que la clave se encuentre.
  **(4) Una frase, un nodo.** Un párrafo partido por dentro con un `<code>` produce trozos («, necesitan») que no
  se pueden traducir por separado. Había uno y se reescribió para que el `<code>` quede al final. La regla es la
  misma que en Lua: nunca media frase.
  **(5) Los dos fallos de la sala que esto deja cerrados** los cazaron los tests de navegador, que son los únicos
  que ejecutan estas páginas de verdad: `var t = document.createElement('span')` tapaba el `t()` global (ADR-109) y
  un renombrado a ciegas de esa misma variable convirtió una **clase de CSS** (`'t'`, el título de la fila) en
  `'task'`, que era la de la fila entera, así que cada descarga contaba por dos. Un renombrado automático no
  distingue un identificador de una cadena: por eso ahora las páginas pasan por `node --check` y por un test que
  prohíbe la variable `t`.

- ADR-108 · Un repaso de traducción es también un repaso de lo que el programa entiende (H49/G7).
  Leyendo los 1.533 pares salió una sola frase con castellano dentro en los otros dos idiomas: la pista de la
  programación manual, «también **mañana** 9:00 1h30». Y no era un descuido de la traducción: el analizador de
  `mpvd/iptv/schedule.py` solo entendía `hoy`/`mañana`/`pasado`, de modo que la pista en inglés o en francés tenía
  que seguir diciendo una palabra en castellano **para que funcionara**. Traducir la pista sin tocar el analizador
  habría dado una instrucción que no funciona, que es peor que no traducirla.
  **La decisión es que el analizador entienda los tres idiomas** (`today`/`tomorrow`, `aujourd’hui`/`demain`/
  `après-demain`, los conectores `to`/`à`/`until`, `now`/`maintenant`), y que las castellanas sigan valiendo
  siempre: una lista guardada o una costumbre de los dedos no se rompe por cambiar de idioma. Los avisos de esa
  entrada («no entiendo la hora…»), que salen en la paleta, pasan por `t()`; y el `hoy 21:30–22:15 (45 min)` de la
  ficha también, que es lo que se ve en el menú.
  **Lo que el repaso NO cambió**: la puntuación francesa ya estaba bien (espacio antes de `:` `;` `!` `?`,
  comillas `« »`) y no había ni una traducción vacía ni un `%s` descolocado —eso lo vigila un test—. Lo único
  cosmético fue unificar el apóstrofo tipográfico (`’`) en las 267 cadenas francesas que llevaban el recto.

- ADR-109 · `t` es un nombre demasiado corto para dejarlo suelto: se prohíbe como variable (H49).
  La función de traducción se llama `t()` en los tres lenguajes del proyecto porque va en 1.538 sitios y un nombre
  largo haría el código ilegible. El precio es que `t` es también el nombre que todo el mundo le pone a una
  variable temporal —una pista, una tarea, un `<span>`— y entonces **tapa la función**, de tres maneras distintas
  y las tres silenciosas:
  **En Lua**, un `local tr` dentro de una función tapa el `tr` del módulo; reventó en mu-subs y lo cazó luacheck.
  **En JavaScript**, `var t = …` se iza al principio de la función, así que la línea de ANTES ya falla con «t is
  not a function»; en la sala eso dejaba la lista de invitados, el chat y el vídeo sin pintar, y nadie lo veía
  porque el error se lo comía un `catch`.
  **En Python es lo peor**: una sola asignación `t = …`, o un `for t in …`, hace el nombre local de **toda** la
  función, así que `raise RpcError(NOT_FOUND, t("no task %s") % (id,))` no lanza el error que dice sino un
  `UnboundLocalError`. O sea: el camino del error se rompe exactamente cuando hay un error. Pasó en tres sitios
  (`asr.status`, `asr.segments` y el idioma de origen de los subtítulos) y lo cazó **la pasada completa**, no el
  lint: para ruff y para mypy ese código es correcto.
  **La decisión es que ningún `t` suelto se queda**, y que lo vigile un test en vez de la memoria: uno recorre el
  JavaScript de las páginas buscando `var|let|const t`, `function (t)` y `(t) =>`, y otro recorre mpvd con `ast`
  y marca cualquier función que **ate** el nombre `t` y además llame a `t(...)` —mirando el ámbito de verdad, sin
  entrar en las funciones anidadas ni en las comprensiones, que tienen el suyo—. Un `for x in …` no cuesta nada;
  un error que se convierte en otro error, mucho.

- ADR-110 · Lo que falla en la prueba de Ser manda sobre lo que yo creía terminado (H66).
  Tres cosas de su prueba del 2026-10-04, y las tres enseñan algo distinto.
  **(1) Un ajuste sin fila no existe.** H59 construyó los interruptores de los torrents (`torrent.json`, con sus
  métodos `torrent.settings.*`) y la pista de la puerta decía «se encienden en Preferencias › Torrents»… pero esa
  fila nunca se añadió. Para quien usa el programa, eso no es «un ajuste avanzado»: es que **no se puede
  encender**, y la pista además miente. La decisión es la de siempre (H58/K2) y aquí se incumplió: si el programa
  nombra un sitio, ese sitio tiene que existir. El test que lo cierra abre la fila y la pulsa.
  **(2) Lo que la gente hace es arrastrar, no pegar.** La puerta única reconocía magnets desde el primer día, pero
  solo escribiéndolos o pegándolos; soltar un `.torrent` en la ventana no hacía nada, porque un fichero soltado va
  derecho a `loadfile` y mpv no sabe abrirlo. **Se intercepta el fallo, no la carga**: el `on_load` apunta la ruta
  si parece un torrent y, cuando el `end-file` dice «error», se pide a mpvd la dirección local y se reproduce. Era
  la única forma limpia, porque abrir un torrent es asíncrono (hay que esperar los metadatos) y un hook de mpv es
  síncrono: no se puede «rehacer» la carga desde dentro del hook. Es exactamente el mismo camino que ya usaba el
  reintento de yt-dlp, así que no hay maquinaria nueva. De paso entra el `.torrent` de la web, que es lo que se
  arrastra desde el navegador: mpvd lo baja (tope de 8 MB y comprobando que empieza por bencode) y lo añade.
  **(3) Una franja no le quita la ventana a nadie.** Hasta ahora, «pon música a las 21:00» se cargaba en la
  ventana que estuvieras usando, que es exactamente lo contrario de lo que se espera de una alarma. Ahora abre la
  suya con `--window-maximized=yes --force-window=yes` —para que, cuando el equipo se despierta solo (H40), lo que
  aparezca se vea de lejos— y **la cierra al acabar la franja**, porque una ventana que abrimos nosotros y se queda
  vacía es basura que se acumula. La reutilización sigue disponible (`MPVD_SCHEDULE_WINDOW=reuse`) y es lo que usan
  los tests, que no pueden abrir ventanas; que la nueva se abre de verdad y con el argumento puesto lo comprueba un
  test con un envoltorio de mpv que apunta su propia línea de órdenes.
  **(4) Varios archivos son UNA cosa: un `.m3u8`.** Programar admite una sola cosa, así que marcar cinco canciones
  necesitaba un envase. Se escribe un `.m3u8` en la carpeta de datos (`files.playlist`) y a partir de ahí es el
  camino que J5 ya dejó hecho: `loadlist` y repetir mientras dure la franja. No se usan las listas de Música para
  esto: una selección de paso no debería aparecer para siempre en la biblioteca musical de nadie.
  **(5) Y el reparto Enter/Tab**, que en el salón importa: Enter programa ese archivo (el caso normal) y Tab lo
  añade a la selección. El mando del televisor no tiene Tab, y por eso la carpeta entera es una **fila** y no una
  acción de Tab (la misma regla de ADR-103).

- ADR-111 · Los torrents salen del programa (Ser, 2026-10-04). **Sustituye a ADR-106**, que queda no vigente.
  Ser los probó, no le funcionaron y lo zanjó: «todas las funcionalidades torrent fuera; si no son cosas críticas
  no pasa nada, quiero que el reproductor funcione y lanzarlo».
  **Por qué es la decisión correcta y no una rendición.** Un torrent depende de tres cosas que el programa no
  controla: que haya pares, que la red deje pasar DHT y que esté instalado un extra que no viene de fábrica.
  Cuando falla cualquiera de las tres, lo que se ve es «no funciona» —indistinguible de un fallo nuestro—, y
  diagnosticarlo exige un torrent vivo y una red que colabore. Para una función **que no es crítica** eso es un
  coste de soporte desproporcionado, y encima es la parte del programa que más puede manchar lo demás: el día que
  alguien pruebe el reproductor y lo primero que no le funcione sea el torrent, el juicio no será «el torrent no
  va», será «esto no va».
  **Se quita de verdad, no se esconde**: fuera `mpvd/torrent/` entero, su registro en el servidor, el extra
  `libtorrent`, el reconocimiento de magnets en la puerta única, la fila de Preferencias, el enganche que abría un
  `.torrent` arrastrado, las 28 cadenas de los catálogos y su test. Esconder una función deja código muerto que
  hay que mantener, traducir y probar; quitarla deja el programa más pequeño, que es lo que se pidió.
  **Lo que NO se pierde**: lo medido y lo aprendido se queda escrito (ADR-106 sigue ahí, no vigente, y el BACKLOG
  conserva el diseño). Si algún día se retoma, lo que importa es que la pieza clave era el **lector** con
  `set_piece_deadline` sobre la ventana de reproducción —no el cliente— y que mpv no necesita saber nada de
  torrents si se le sirve por HTTP con Range. Y queda intacto lo que vino de ahí y sí es del programa: servir un
  fichero local por HTTP con Range (H44/C1) y explorar las carpetas del equipo (H64).
  **Y una lección que se queda**, de P1 de H66: un ajuste que el programa **nombra** y no existe no es «un ajuste
  avanzado», es un ajuste que no se puede cambiar. Si una pista dice «se enciende en X», X tiene que existir.

- ADR-112 · El vigilante de los menús pregunta antes de olvidar (H63/N3). **Amplía ADR-104** (H65), que arregló
  una cara de esto en TV y dejó el resto a oscuras.
  **La causa, con los milisegundos del log de una pasada que falla.** Cada módulo tiene un vigilante: «si 0,2 s
  después de que el menú abierto deje de ser el mío sigue sin haber uno mío, doy por cerrada la navegación y la
  olvido». Existe porque cerrar con Esc no se anuncia de otra manera. El problema es **cuándo se arma**: se arma
  cuando aparece el menú de **otro** módulo —el del padre, por ejemplo—, así que el reloj de mu-convert empezó a
  correr en 3,010 s (cuando se abrió «Descargas y conversión»), mu-convert entró en su vista en 3,19 s, el
  vigilante disparó en 3,210 s y en ese instante uosc **todavía no había publicado** el menú nuevo: lo publicó en
  3,223 s. Entre pedir y confirmar hay normalmente 1 ms; con el equipo ocupado se midieron **27 ms**, y el
  vigilante cayó justo dentro. Resultado: el menú sale bien en pantalla y el módulo se ha olvidado de dónde
  estaba. Se ve en que ⌫ salta de nivel en vez de subir uno, en que lo que estaba cargando no llega a aparecer
  (las respuestas comprueban «¿sigo en esta vista?» y ya no lo están) y en que los paneles que se refrescan solos
  se quedan quietos. Pulsar una fila sigue funcionando, y por eso en uso normal se nota poco.
  **El arreglo**: `mu.uosc` apunta qué menú se ha pedido y cuándo (`M.open`), lo da por confirmado en cuanto uosc
  publica ese tipo, y expone `asking(tipo)`. El vigilante de los catorce módulos pregunta antes de olvidar nada.
  Dos líneas en el módulo y una en cada vigilante; **no cambia cómo se abren los menús**, que es lo que hundió el
  intento anterior. Medido: el test que fallaba 1 de cada 3 pasa **8 de 8**.
  **TRES intentos anteriores, descartados, para que nadie los repita**: (a) rellenar en `Nav:frame` el nivel que
  falta cuando la pila está vacía —correcto en sí, pero no era la causa—; (b) que `uosc.open()` no reabriera lo ya
  pedido y actualizara en diferido —peor, 6 de 6: uosc descarta `update-menu` si el menú no está abierto todavía—;
  (c) subir el permiso `opening` de H65 a `mu.uosc` y exigirlo para abrir —en TV funciona, pero fuera de TV hay
  caminos que abren menú sin pasar por `open_view`, así que esos menús dejaban de aparecer y la pasada subió de 1
  fallo a 4—. Los tres atacaban el segundo `open-menu`, que es un **síntoma** (hace que uosc cierre y reabra, un
  parpadeo) y no quien borraba la navegación.
  **Y un fallo del propio test, encima del de verdad**: `item(v, "Bitrate del audio")["hint"]` dentro de un
  `wait_property` revienta cuando la primera lectura llega sin filas, y eso tumba la espera entera en vez de
  volver a mirar. El mismo fichero lo tenía escrito como advertencia tres líneas más arriba y esa línea se olvidó.
  Arreglado con `(item(...) or {}).get(...)`, que es lo que hacen las demás.

- ADR-113 · Una tabla de datos se traduce donde se sirve, y un mensaje de una petición habla el idioma de quien la
  hace (H49/G8).
  **(1) Dónde se traduce una tabla.** Los formatos de «Convertir» y de «Descargar», los modelos de voz, los
  nombres de las tareas o los motores de traducción son **datos**: listas de diccionarios en un módulo. Envolver
  ahí la cadena con `t()` sería un error silencioso —se evaluaría **al importar el módulo**, con el idioma de ese
  instante, y se quedaría fijada para siempre—. Así que la tabla se queda en castellano (que es la clave) y
  traduce **el punto de uso**: `usable_presets()`, `preset_rows()`, `job_label()`, `to_dict()`. Una línea por
  tabla, y el idioma correcto en cada petición.
  Para que el catálogo no se quede cojo, el extractor recoge esas cadenas con `ast` **por el nombre del campo**
  (`label`, `hint`, `title`, `description`, `note`), sin mirar si «parecen castellano»: «alta» o «normal» no lo
  parecen y son nombres de calidad que hay que traducir igual. Se dejan fuera `name`, `reason` y `text`, que en la
  mitad de los sitios llevan un código interno (`missing`, `hello`) y no una frase; las frases que vivían en esos
  campos se envolvieron a mano. Y dos tablas pasaron de tuplas a diccionarios para que el extractor las vea, que
  es más barato que enseñarle a leer tuplas.
  **(2) El idioma de una petición no es el del equipo.** Un `HttpError` de la sala o del mando lo lee el invitado
  en su navegador (ADR-087), pero `t()` usaba el idioma del sistema del anfitrión. Pasar el idioma como parámetro
  hasta cada `raise` habría tocado decenas de funciones, muchas de ellas ajenas a HTTP. Se resuelve con una
  **variable de contexto**: `handle()` fija el idioma del visitante al entrar y lo deshace al salir, y `t()` lo
  consulta antes del idioma del sistema. Cada petición es una tarea de asyncio y cada tarea tiene su copia, así
  que no hay fugas entre visitantes —lo comprueba un test que pide el mismo error en tres idiomas seguidos—.
  **(3) Lo que NO se traduce, y por qué**: los ~240 nombres de país de `iptv/labels.py` son datos de la lista de
  canales (traducirlos es un trabajo de datos, no de interfaz), las descripciones de `mcp.py` las lee un modelo y
  no una persona, y los prompts de `llm.py` ya están escritos por idioma. El extractor los salta por nombre de
  fichero, para que el test no los reclame.

- ADR-114 · Reproducir manda: mientras se ve algo, nada que no se haya pedido (H69/H70).
  Lo levantó Ser con la prueba que importa: una película HEVC 10 bits daba tirones con Atalaya y, **en el mismo
  momento**, iba bien con el mpv de apt. Medido en su portátil (i5-6200U, 4 núcleos), y lo primero que apareció no
  fue un fallo sino un **suelo**: este equipo no decodifica HEVC 10 bits por hardware (VA-API solo ofrece
  `VAProfileHEVCMain`), así que esa película la decodifica la CPU en cualquier reproductor: 29 % de un núcleo con
  un 1080p10 fácil, **3,3 núcleos** con uno de 39 Mbps. Lo que queda de margen es lo que no hay que gastar.
  **(1) Un reproductor no tiene por qué tener a nadie detrás.** La regla del proyecto ya decía «lo pesado nunca
  compite con la reproducción» y estaba aplicada a las conversiones, la retransmisión de salas, las suscripciones
  y las etiquetas de música… y **no a los tres procesos más caros**: `whisper-cli`, `llama-cli` y el `fpcalc` +
  `ffmpeg` que busca la intro **al abrir el archivo, sin que nadie lo pida**. Eso es exactamente «con Atalaya da
  tirones y con mpv no», porque mpv no tiene a nadie haciendo trabajo. Ahora hay un solo sitio
  (`mpvd/priority.py`) que baja CPU **y disco**: `nice 15` para lo que puede durar media hora, `nice 10` para el
  resto y la prioridad de E/S en clase `idle`, que es la que importa cuando el trabajo de fondo lee un archivo de
  varios GB mientras el reproductor lee otro. Medido: 47 → 28 fotogramas perdidos con tres codificadores de fondo.
  **(2) Y la regla de Ser, que es mejor que bajar prioridades**: mientras hay algo reproduciéndose, **no arranca
  nada especulativo**. Lo barato es que la clasificación ya existía: `URGENT` es un subtítulo que va a salir ya,
  `INTERACTIVE` es lo que ha pulsado la persona, y `PRECOMPUTE`/`INDEX` es todo lo que el programa se inventa
  (analizar la intro, pre-subtitular el siguiente episodio, indexar la biblioteca, la guía, las suscripciones).
  Así que la regla son tres líneas en la cola —no se saca de la cola nada con prioridad ≥ PRECOMPUTE mientras
  `sessions.playing()`— y una en las sesiones, que despiertan la cola al pausar o parar. `pause` y `path` ya se
  observaban, así que no cuesta ni una llamada a mpv. El guardián reactivo (si se pierden 2 fotogramas por segundo,
  diez segundos sin trabajo pesado) se queda como segunda red, pero ya no es la primera: reaccionaba **después** de
  que el tirón se viera, y volvía a intentarlo cada diez segundos.
  **(3) thumbfast**, que es lo más parecido a un segundo reproductor: arranca **otro mpv** para las miniaturas al
  pasar el ratón por la barra (38,8 % de un núcleo decodificando la misma película) y estaba con
  `quit_after_inactivity=0`, es decir, **no se cerraba nunca** una vez abierto. Ahora se cierra a los 10 s de no
  usarlo; volver a abrirlo cuesta un par de décimas.
  **(4) Lo que NO se ha reproducido, y conviene decirlo**: el caso exacto de Ser. En las medidas controladas
  Atalaya sale igual o mejor que mpv a secas (con carga, 0 fotogramas perdidos frente a 50). Mis pruebas no tocaban
  el ratón —así que thumbfast no arrancaba— y la carpeta de datos estaba vacía —así que no había intro que analizar
  ni biblioteca que indexar—, que son justo los dos mecanismos encontrados. La confirmación tiene que venir de
  volver a poner esa película.
- ADR-115 · La película de Ser, medida con los dos reproductores: lo que cuesta Atalaya es la interfaz, y a
  pantalla completa no cuesta nada (H71).
  Cierra el punto (4) de ADR-114, que quedó abierto a propósito: «la confirmación tiene que venir de volver a
  poner esa película». Ser la puso: `Silencio` (Scorsese, 2016), MKV de 2,7 GB, **HEVC Main 10**, 1920x804,
  yuv420p10le, 23,976 fps, 2,4 Mbps, 161 min, dos pistas AC3 y tres de subtítulos. Minuto 20, ventanas de 25 s,
  con `--ao=null` y con ventana de verdad, porque el gasto que se buscaba está en pintar.
  **Lo primero fue aprender a medir**, porque las primeras tandas se contradecían entre ellas. Cinco errores, los
  cinco corregidos en `tools/comparar.py`, que queda en el repo para poder repetir esto cuando haga falta:
  1. el intérprete del `.venv` se llama **`python`** en `/proc`, no `python3`: con la lista que había, **mpvd no se
     contaba en ninguna cuenta**. El mismo error estaba en `tools/diagnostico.py`, que ya llevaba commit.
  2. alternando A, B, A, B el segundo de cada pareja mide siempre con la CPU más caliente. El orden pasa a ser
     **A, B, B, A**, con las mismas aperturas para cada uno.
  3. **el compositor trabaja por el reproductor** y tampoco se contaba: `kwin_wayland` gasta ~5 puntos de un
     núcleo con cualquiera de los dos. Llegó al 52 %, con `polkitd` al 30 % y `dbus` al 16 %, mientras mis propias
     pruebas abrían y cerraban ventanas sin parar: eso, y una **carga media de 5 en una máquina de 4 núcleos**, es
     lo que Ser tenía por detrás cuando vio los tirones.
  4. **`/proc/<pid>/task`**: mpv nombra sus hilos (`av`, `vo`, `demux`, y uno `lua/<script>` por script), así que
     el gasto se parte por hilo en vez de ir quitando piezas a ciegas. Esto es lo que señaló al responsable.
  5. y el que lo explicaba todo: **el tamaño de la ventana lo elige el escritorio**, y cambia entre aperturas (se
     vieron 1366x573 y 1920x804 en la misma tanda). El OSD se rasteriza a tamaño de ventana y el vídeo se escala a
     ella, así que la misma configuración medía 30 % o 40 % según el tamaño que le hubiera tocado. Ahora la
     herramienta fija `--autofit` y `--geometry` iguales para los dos, y avisa a gritos si no coinciden.
  **Lo medido, con los dos reproductores y la misma película** (más de 40 aperturas):
  * **Ni un fotograma perdido en ninguna**, con ninguno de los dos. La película no es pesada: 2,4 Mbps y **0,28
    núcleos**. Los dos la decodifican **por software**, porque este equipo no tiene HEVC 10 bits por hardware.
  * **A pantalla completa, que es como se ve una película, gastan lo mismo**: mpv 33,5 % y Atalaya 34,3 % de un
    núcleo contando TODA la pila, o sea **+0,8 puntos, por debajo del ruido de la máquina (±2,7)**. Por hilo:
    descodificar 21,6 contra 21,4 (idéntico), pintar 4,8 contra 5,1, y la interfaz **`lua/uosc` 1,2 contra el
    `lua/osc` 0,6 que gasta la de mpv**. Seis décimas de más por tener nuestra interfaz.
  * **En ventana, con la ventana fijada igual para los dos** (1280x720, cuatro medidas cada uno con el ruido en
    ±0,6): mpv **35,0 %** y Atalaya **36,2 %**, o sea **+1,1 puntos**, y por hilo se ve de dónde sale: descodificar
    21,5 contra 21,3, pintar 4,9 contra 5,4, el hilo principal 1,2 contra 1,4, y la interfaz **`lua/uosc` 1,2
    frente al `lua/osc` 0,6 de mpv**. Todo el exceso es la interfaz; **los 23 scripts propios cuestan 0,0 y mpvd
    cuesta 0,0** (con uosc solo, con los 23 cargados y con Atalaya entera sale el mismo número): durante la
    reproducción el demonio solo observa cuatro propiedades que no cambian —`frame-drop-count`, `pause`, `path`,
    `media-title`— y H70 no le deja arrancar nada especulativo.
  * **Y la medida que casi me hace escribir una tontería**: antes de fijar la ventana, Atalaya salía +5 puntos en
    ventana, repetido y clavado. No era uosc: era que mpv a secas y Atalaya tienen distinto identificador de
    aplicación (`wayland-app-id=mpv-uos`), así que el escritorio les daba ventanas de distinto tamaño, y el OSD se
    rasteriza al tamaño de la ventana. Comparar dos ventanas distintas no compara nada. Queda escrito aquí porque
    es el error que más veces estuvo a punto de colarse.
  **Decisión**: no se toca uosc ni su configuración. El exceso que se puede atribuir a este proyecto es **1,1
  puntos de un núcleo** —el 0,3 % de esta máquina de cuatro— y es el precio de tener interfaz; la de mpv cuesta
  0,6 y la nuestra 1,2. Y lo que de verdad importaba: **ni un fotograma perdido**. Si algún día molesta, la
  palanca medida es apagar los elementos de uosc por su API pública (`disable-elements`) mientras nadie toque
  nada, no parchearlo.
  **Lo que sí se quitó de lo nuestro**, por la regla de Ser («que pase de cualquier cosa que no sea
  indispensable»): `mu-menu` y `mu-record` observaban `time-pos`, o sea que mpv les despertaba **24 veces por
  segundo** para apuntar un número que `mu-menu` usa cada 15 s al guardar la posición y `mu-record` solo al cerrar
  un archivo mientras graba un trozo. Ahora `mu-menu` lo refresca una vez por segundo mientras hay algo abierto y
  `mu-record` lo apunta en el tick que ya dibuja el contador de grabación. En los scripts propios no queda ningún
  observador por fotograma.
- ADR-116 · Programar en días de la semana: se guarda la REGLA y solo hay una franja pendiente por serie (H67).
  Ser lo pidió así: «quiero que si se dice de l-v, cada día, etc, siga así de forma indefinida, hasta que el
  usuario indicara lo contrario». Lo que había materializaba «las próximas dos semanas» como franjas normales, que
  es justo lo que él no quería: se acaban.
  **(1) La regla vive en la franja, y de cada serie hay UNA pendiente.** La alternativa —escribir en la lista todas
  las ocurrencias futuras— obliga a elegir un horizonte (y entonces la repetición se acaba), llena la lista de
  filas que nadie ha pedido, multiplica los avisos de solape y complica el despertador, que se pone para la
  primera pendiente. Aquí cada franja lleva `repeat` (`daily`/`weekdays`/`weekly`), `days` (0 = lunes … 6 =
  domingo) y `series` (el id de la primera), y **la siguiente se crea cuando esta termina**: en `_finish`, así que
  da igual si acabó bien, falló, se perdió o se canceló.
  **(2) Indefinido de verdad quiere decir sobrevivir a tres cosas**, y cada una tiene su sitio: que el equipo
  estuviera apagado (`recover()` marca la perdida y deja puesta la siguiente, así que una semana de vacaciones no
  mata la serie), que se cierre mpvd (la regla está en `iptv-schedule.json`, se relee) y que alguien se salte un
  día (ver abajo). Un test la hace rodar sesenta veces seguidas comprobando que nunca hay dos pendientes ni cero.
  **(3) «Hoy no» y «ya no más» no son lo mismo**, y antes solo había una tecla: `cancel` cancela ESTA franja y la
  serie sigue (la siguiente queda puesta), `remove` acaba con la serie entera, y `iptv.schedule.repeat` quita la
  repetición dejando la franja que ya estaba puesta. En la lista son tres filas con ese nombre exacto —«Saltarse
  solo esta vez», «Dejar de repetir», «Quitar la serie entera»—, porque una sola «Cancelar» no dice cuál de las
  tres cosas va a pasar.
  **(4) La repetición se lee ANTES de partir el texto en palabras.** El analizador de horas se come los conectores
  (`de`, `a`, `hasta`), así que «de lunes a viernes» le llegaría como «lunes viernes», que es otra cosa: lunes Y
  viernes. Un prefijo propio reconoce «cada día», «de lunes a viernes», «l-v», «laborables», «fines de semana»,
  «los sábados», «martes y jueves» y «martes a jueves», en los tres idiomas (ADR-108: quien escribe la hora está
  en el idioma del reproductor), y lo que queda se analiza como siempre. La primera franja de la serie es la
  primera que toque: «de lunes a viernes 21:30» escrito un sábado empieza el lunes.
  **(5) La hora es la del reloj, no un número de segundos.** La siguiente se calcula sobre el calendario con
  fechas sin zona, así que «cada día a las 21:30» sigue siendo a las 21:30 el día que cambia la hora; sumando
  86.400 segundos habría pasado a las 20:30. También se conserva la hora de fin (una franja que cruza la
  medianoche sigue cruzándola), con un suelo: si la reconstrucción al minuto dejara el fin antes del inicio
  —franjas de segundos, que solo salen en los tests—, manda la duración.
  **(6) Grabar varios canales a la vez SÍ; sonar dos cosas a la vez, no.** Cada grabación es su propio ffmpeg
  copiando el flujo (`-c copy`), así que dos a la vez cuestan dos veces casi nada: probado de punta a punta con
  dos canales y ffmpeg de verdad, los dos archivos con vídeo, audio y duración. Pero altavoces hay unos, así que
  programar una REPRODUCCIÓN que pisa a otra **avisa y no impide**: avisar es información, impedir sería decidir
  por quien lo programa, que a lo mejor quiere justo eso (cambiar de una a otra).
- ADR-117 · Los paquetes: `.deb` y AppImage para las dos arquitecturas, `.zip` en Windows, Homebrew en macOS (H72).
  Decidido con Ser el 2026-10-04: «yo haría deb y appimage, ambos tb para arm», «mejor windows zip que exe o msi»
  y «no voy a pagar ninguna cuenta de mac». Esto es cómo se ha hecho y qué se ha podido comprobar de cada uno.
  **(1) Lo que NO va dentro: mpv.** Lo decidió ADR-067 y lo confirmó midiendo ADR-115. Un mpv propio dentro del
  paquete se lleva por delante la aceleración por hardware, que depende de los drivers de la máquina —en este
  portátil, de VA-API—, y eso se paga en cada película. Así que `Depends: mpv` **sin versión mínima**: en Debian 13
  y en Raspberry Pi OS el del sistema puede ser más viejo que el probado, y es mejor instalarse y avisar al
  arrancar de lo que no va a funcionar que negarse a instalar. El aviso lo da el lanzador, que es quien ejecuta
  mpv y puede preguntarle la versión, y lo enseña mu-core una sola vez, nombrando las dos cosas concretas que
  dependen de 0.41: el índice del vídeo (hay que poder ESCRIBIR `chapter-list`) y copiar enlaces (`clipboard/text`,
  que si no cae al camino lento).
  **(2) Lo que SÍ va dentro: el intérprete.** Un CPython 3.12 reubicable (python-build-standalone, el mismo que
  gestiona uv), el mismo tarball fijado con su SHA-256 para las cuatro variantes. Así el paquete no depende de la
  versión de Python de la distribución, que es justo el problema que tienen Debian 13 y Raspberry Pi OS. El precio
  está medido y se asume: 27 MB el `.deb` de amd64, 21 el de arm64, 38 y 34 los AppImage.
  **(3) Cruzar a ARM sin una máquina ARM**, que era la parte dudosa y sí se puede: el intérprete se baja ya
  compilado para aarch64 (mismo origen, SHA-256 fijado), las dependencias se instalan con `uv pip install
  --python-platform aarch64-unknown-linux-gnu --only-binary :all:`, y para el AppImage el appimagetool de x86-64
  construye el de aarch64 si se le da su `runtime` con `--runtime-file`. Lo que sale está revisado por dentro
  (intérprete ARM de verdad, runtime ARM, catálogos de idiomas presentes) y **no se puede ejecutar aquí**: eso
  queda dicho en docs/PLATAFORMAS.md y en NEEDS_HUMAN.md, no escondido.
  **(4) La regla de `sudoers` del despertador, que es lo único que el `.deb` puede hacer y el AppImage no.** Y
  aquí apareció lo que no estaba previsto: **sudo ya no acepta comodines en los argumentos** («wildcards are not
  allowed in command arguments»), así que no hay forma de escribir una regla que permita «rtcwake -m no -t <un
  número>» y nada más. Las opciones eran permitir `rtcwake` entero —y con él `rtcwake -m off`, que apaga la
  máquina— o poner la validación en un programa nuestro. Se hizo lo segundo: `bin/wake`, catorce líneas, que solo
  sabe poner la alarma (y solo si lo que recibe son dígitos), borrarla y decir si se puede, y que rechaza hasta un
  argumento de más. La regla autoriza ese fichero y nada más; en el paquete es de root, así que nadie puede
  cambiar lo que se ejecuta con permisos. En un clon del repositorio el fichero es de quien lo usa, así que ahí NO
  se instala ninguna regla: sería darse permisos a uno mismo. El postinst valida la regla con `visudo -c` y, si no
  fuera válida, **la borra**: es mejor quedarse sin despertador que dejar a alguien sin poder usar sudo.
  **(5) Lo que enseñó revisar el paquete con `lintian`**, que es el equivalente a pasar el lint al código: de 2.780
  avisos a 15. Los 2.758 primeros eran una tontería con consecuencias —los ficheros del repositorio llevan los
  permisos del umask de quien construye (0664/0775) y un paquete se instala con 0644/0755—, y los demás eran
  basura de verdad que viajaba dentro: Tcl/Tk completo (con un RPATH a la máquina donde se compiló el intérprete),
  pip, los tests de las dependencias y, lo más vergonzoso, un `__pycache__` que metía **mi propia comprobación**
  del intérprete al ejecutarlo sin `-B`. Los 15 que quedan son inherentes a llevar el intérprete dentro (trae
  zlib, bzip2, expat y ncurses enlazados) y están en una lista blanca dentro del test: si aparece otro, el test lo
  dice. Y una lección con nombre propio: **despojar de símbolos el binario del intérprete lo deja sin arrancar**
  («undefined symbol: , version»), mientras que despojar sus bibliotecas va bien; lo cazó la comprobación que
  ejecuta el intérprete recién empaquetado, que por eso está ahí.
  **(6) El fallo más grave de todo esto no era del paquete, era del lanzador.** Leer la versión con
  `mpv --version | head -1` le cierra la salida a mpv, que muere con SIGPIPE, y como `bin/mpv-uos` corre con
  `set -o pipefail` eso **abortaba el lanzador antes de abrir el reproductor**, sin decir nada. No lo vio ningún
  lint: lo vio el test que extrae el `.deb` y comprueba que arranca y que mpvd se conecta. Se lee con `awk`, que no
  cierra ninguna tubería, y hay diez tests nuevos que ejecutan el lanzador con un mpv de pega que escribe 2.000
  líneas y con salidas raras, para que esto no vuelva.
  **(7) Windows: `.zip` portable.** Un `.exe` o un `.msi` sin firmar se come el aviso de SmartScreen, que asusta
  más que descomprimir una carpeta; y la firma cuesta dinero y caduca. El zip lleva la aplicación, un CPython para
  Windows puesto exactamente donde lo busca `bin\mpv-uos.ps1` (`.venv\Scripts\python.exe`), `yt-dlp.exe` fijado y
  comprobado, el ayudante de uosc de Windows **y solo ese** (los de Linux y macOS son 11 MB que ahí no se usan),
  el `install.ps1` que ya existía para quien quiera accesos directos y los enlaces `mpv-uos://`, y un LEE-ME con
  saltos de línea CRLF que dice tres cosas: cómo abrirlo, que mpv se instala aparte (`winget install mpv`) y que
  esto **no se ha podido probar en un Windows de verdad**.
  **(8) macOS: por Homebrew, y dicho claramente.** Sin cuenta de Apple no hay firma, y un `.dmg` sin firmar lo
  bloquea Gatekeeper con un mensaje que habla de malware: dar eso es peor que no darlo. Además el `.app` que ya se
  construye no sería autocontenido (usa el mpv de Homebrew). Si algún día hay cuenta, lo que falta es firmar y
  notarizar ese mismo `.app`.
- ADR-118 · Avisar de que hay versión nueva: una petición al día, y nunca instalar nada (H68).
  Ser lo pidió al decidir los paquetes: «¿es posible que la app te avise cuando haya actualizaciones
  disponibles?». Sí, y la parte importante de la respuesta es dónde está el límite.
  **(1) Avisar, no actualizarse.** Un programa que se actualiza solo tiene que descargar, verificar, reemplazarse
  **mientras está en marcha** y saber volver atrás si la versión nueva no arranca. Las dos últimas son la parte
  difícil y, hechas mal, dejan a alguien sin reproductor; y en Linux eso ya lo saben hacer `apt` y el gestor de
  AppImage de cada uno, que además pueden pedir contraseña cuando toca. Así que esto solo dice «hay una nueva»,
  con el enlace y —si el fichero los trae— el SHA-256 de cada paquete, para quien quiera comprobar lo que
  descarga. **Lo vigila un test**: `mpvd/updates.py` no puede contener `chmod`, `subprocess`, `tarfile`,
  `zipfile`, `shutil.move` ni un segundo `os.replace`, y no puede tener una función `install` ni `apply`. Si
  alguien cruza esa línea, el test lo dice.
  **(2) Dónde vive el fichero**, que era la decisión pendiente: en **el sitio del proyecto**, `<site>/latest.json`,
  con `site` saliendo de `brand.json` para que no haya dos sitios donde cambiarlo. No se usa la API de releases de
  GitHub porque eso exige que el repositorio sea público, y esa decisión es de Ser y está sin tomar; cuando la
  tome, basta cambiar la URL (o poner `MPV_UOS_UPDATE_URL`). Y **el `latest.json` lo genera `tools/build_web.py`**
  de los paquetes que hay en `dist/`, con el tamaño y la suma de verdad de cada uno: la página y el aviso salen
  del mismo dato, así que no pueden contradecirse. Mientras la web no esté publicada, la consulta da 404 y el
  programa calla: eso no es un fallo, es el estado normal hasta que haya web.
  **(3) Una petición al día, y solo en los paquetes.** La misma caché HTTP que la comprobación diaria de yt-dlp
  (petición condicional, y si la red falla se sirve lo último que hubiera). En una copia del repositorio está
  **apagado**, porque ahí se actualiza con `git pull` y avisar sería ruido; se enciende solo si el programa viene
  de un paquete (`MPV_UOS_PACKAGED`, que pone el lanzador del `.deb`, o `MPV_UOS_APPIMAGE`). Y «apagado» quiere
  decir que **no se hace ninguna petición**: lo comprueba un test contando las que llegan a un servidor local.
  **(4) Se dice una vez, no cada vez que se abre.** Lo que molesta de los avisos de actualización no es el aviso,
  es el tercero. mpvd recuerda en `updates.json` cuál fue la última versión anunciada y solo contesta `announce:
  true` la primera vez; una versión aún más nueva vuelve a avisar. Y se pregunta **medio minuto después** de que
  el demonio conecte, no al abrir: lo primero que tiene que pasar al abrir un reproductor es que se vea la
  película.
  **(5) Lo que no cuenta como versión nueva.** La comparación es por números (`0.2.0` → `(0, 2, 0)`) y lo que no
  sea número se ignora, así que `0.2.0-rc1` no es más nuevo que `0.2.0`: quien no anda buscando candidatas no
  tiene por qué enterarse de que existen. Y nunca se avisa «hacia atrás»: si el fichero anuncia una versión más
  vieja que la instalada —porque alguien se adelantó o publicó mal—, no pasa nada.
  **(6) Ningún identificador.** La petición no lleva nada que distinga a un equipo de otro: ni identificador, ni
  versión del sistema, ni contador. Es una descarga de un fichero estático, igual que la de cualquier página. Si
  algún día se quiere saber cuánta gente lo usa, eso es otra decisión y habrá que tomarla a la cara.
- ADR-119 · La web: una sola fuente para tres idiomas, capturas de verdad y nada que venga de fuera (H50).
  El diseño estaba escrito en docs/SITIO-WEB.md desde el 2026-10-02 y tenía dos cosas esperando a Ser: las
  capturas y si el repositorio se hace público. Se ha construido todo lo que no depende de eso.
  **(1) Una fuente, tres idiomas.** `web/contenido.json` + `web/plantilla.html` y `tools/build_web.py` escribe
  `index.html`, `en/index.html` y `fr/index.html`. Escribir tres HTML a mano garantiza que en un mes digan cosas
  distintas; un test comprueba que los tres idiomas tienen exactamente las mismas claves.
  **(2) Las descargas se leen de `dist/`**, con el tamaño y el **SHA-256 de verdad** de cada archivo, y de ahí
  sale también el `latest.json` que consulta el programa (ADR-118): la página y el aviso de versión no pueden
  contradecirse porque son el mismo dato. Una página que anuncia una suma que no es la del fichero es peor que
  una que no anuncia ninguna, y un enlace a algo que no se ha construido es un 404 en la cara de quien venía a
  probarlo: solo se anuncia lo que existe.
  **(3) Nada de fuera.** Ni un script, ni una fuente de Google, ni cookies, ni analítica —en la página de un
  programa que dice «nada sale de tu ordenador», cargar una fuente de otro servidor sería una broma—. Lo vigila
  un test que además mira los comentarios aparte, porque el propio HTML lleva escrito «ni cookies, ni analítica»
  y hacer saltar el test con eso también sería una broma.
  **(4) Las capturas son del programa de verdad** (`tools/capturas.sh`: abre el reproductor, pide cada pantalla
  por su atajo y guarda `screenshot window`, que incluye la interfaz). Y aquí hubo que parar: **la primera tanda
  salió con el menú enseñando el historial de Ser** —sus últimos vídeos, con sus títulos— camino de una página
  pública. Ahora el guion fuerza una carpeta de datos vacía, el porqué está escrito en el propio guion y hay un
  test que lo vigila. Las capturas se hacen con el vídeo de pruebas del proyecto, no con una película de nadie:
  para la web quedarían mejor con contenido real, y por eso el archivo es un argumento del guion.
  **(5) Lo que no está probado se dice EN la página**, no en una nota al pie: ARM sin ejecutar, Windows sin
  abrir, macOS por Homebrew y por qué. El diseño lo pedía expresamente y hay un test que lo comprueba en los tres
  idiomas: quien se entere después, se va.
  **(6) La página «cómo está hecho» se genera de `docs/ARQUITECTURA.md`**, que es el único texto nuevo que hacía
  falta escribir (el mapa de las dos piezas, cómo se hablan, la caché, qué sale a la red y cómo se prueba). No se
  escribe dos veces: lo demás ya está en `docs/` y en los ADR. El conversor de Markdown son cincuenta líneas sin
  dependencias (`tools/markdown_min.py`), por lo mismo que el servidor HTTP y los códigos QR son propios.
  **(7) Lo que sigue esperando a Ser**: si el repositorio se hace público —sin eso la página exhaustiva no tiene
  dónde vivir y la pública no puede enlazarlo—, dónde se sirve `/atalaya`, y si quiere rehacer las capturas con
  una película de verdad. Está en NEEDS_HUMAN.md.
- ADR-120 · Dos instalaciones con el mismo identificador: la de tu carpeta personal tapa a la del paquete (H72).
  Ser instaló el `.deb` el 2026-10-05 y en el menú le seguía saliendo el de antes. No era un fallo del paquete:
  tenía desde antes la instalación de usuario de `tools/install.sh`, y las dos ponen una entrada de escritorio con
  **el mismo identificador** (`mpv-uos.desktop`). Por la regla de XDG, la de `~/.local/share/applications` gana
  siempre sobre la de `/usr/share/applications`, así que el menú abría el repositorio y desde fuera parecía que el
  paquete no se había instalado.
  **Qué NO se hace**: cambiarle el identificador a una de las dos. El identificador es lo que enlaza los tipos de
  archivo, el `xdg-mime default`, los enlaces `mpv-uos://` y los accesos directos que ya existan; cambiarlo
  rompería todo eso para quien ya lo tenga, a cambio de que en el menú salgan **dos** entradas, que es peor: nadie
  sabría cuál abre qué. Tampoco se toca la carpeta personal desde el `postinst` del paquete: un paquete que borra
  cosas del `$HOME` de alguien está mal, pase lo que pase.
  **Qué se hace**: `tools/install.sh` comprueba si el paquete está instalado y **avisa antes de tapar nada**, con
  los dos comandos para decidir cuál se queda (`tools/install.sh --uninstall` o `sudo apt remove atalaya-player`).
  Está en docs/USO.md, en «Problemas frecuentes», y lo vigila un test con un `dpkg-query` de pega que comprueba
  las dos caras: que avisa cuando el paquete está, y que no dice nada cuando no está.
- ADR-121 · Los vigilantes de los menús esperan por un ESTADO, no por un plazo (H63/N2, parcial).
  Tres pasadas completas seguidas: la segunda salió limpia (1.006 pasando) y las otras dos cayeron por timeouts de
  los mismos sospechosos. El que más se repetía, `test_mu_share::test_menu_create_guests_permissions_close`,
  fallaba **1 de cada 2** al repetirlo aislado.
  **Lo encontrado y arreglado.** Al pulsar una fila, uosc **cierra el menú**; el módulo lo vuelve a abrir cuando
  *mpvd contesta*. Entre una cosa y otra, el vigilante de ADR-112 —«si 0,2 s después de que el menú abierto deje
  de ser el mío sigue sin haber uno mío, olvido dónde estaba»— se dispara, y con el equipo cargado la ida y vuelta
  a mpvd tarda más de 0,2 s. El síntoma era exactamente el de N3: el módulo con `view` vacío, el rastro sin su
  nivel intermedio («Atalaya Player › Invitados» en vez de «… › Compartir › Invitados»). ADR-112 ya cubría «he
  pedido mi menú y uosc no lo ha confirmado»; faltaba **«estoy esperando a mpvd»**. El arreglo es el estado, no
  otro plazo: `mu.rpc` expone `pending()` —cuántas peticiones hay en vuelo, que es dato exacto— y los catorce
  vigilantes no olvidan nada mientras haya alguna. Medido: de fallar 1 de cada 2 a **14 de 15**.
  **Dos hipótesis descartadas con medidas, para que nadie las repita.** (1) «El demonio se bloquea»: se midió con
  un pulso cada 0,25 s durante las pasadas, incluidas las que fallan — **el peor ping fue de 0,03 s**, así que
  mpvd no se para nunca. (2) «La cola de eventos se llena y se pierden peticiones»: `mpvipc` descarta el evento
  más viejo cuando la cola está llena, y por esa cola llegan los `client-message` con las peticiones de los
  scripts, así que era un sospechoso perfecto; se instrumentó y **no se llenó ni una vez**. Lo que sí queda de ahí
  es la instrumentación: descartar es lo correcto —bloquear ahí bloquearía al reproductor— pero ya no es
  silencioso, porque un evento descartado puede ser una petición que nadie va a contestar nunca.
  **Lo que queda, dicho sin adornos.** Sigue habiendo un fallo de cada ~10 en ese test, y la última traza no es el
  mismo camino: el módulo aparece en la vista «Invitados» en un punto donde el test lo espera en la raíz. Eso ya
  no es el vigilante: es que la navegación puede llegar a un estado que el test no contempla, o que el aviso de
  «Ana se ha unido» lo pisa otro aviso posterior. **No se cierra H63/N2** hasta que haya tres pasadas seguidas
  limpias; lo honesto es decir que está acotado y mejorado, no que está resuelto.
- ADR-122 · El cierre en vuelo también es un estado, y un aviso que se pisa no es un observable (H63/N2, cerrado).
  Lo que quedaba de ADR-121 eran **dos** fallos distintos en el mismo test, y los dos eran el mismo error de
  siempre: leer un instante en vez de un estado. Se encontraron con el log completo de mpv de una pasada que
  falla, no razonando.
  **(1) El módulo se cerraba su propio menú recién abierto.** Rastro medido: `2.567 open-menu` · `2.570
  menu/type="mu-share"` · `2.571` el usuario (el test) pulsa la fila del QR y el módulo manda `close-menu` ·
  `2.577` se vuelve a pedir el menú y el módulo manda **`update-menu`** porque `menu/type` todavía dice
  «mu-share» · `2.592` uosc atiende el cierre de 2.571 · `2.693` el menú desaparece. uosc no cierra nunca por su
  cuenta (`update-menu` solo actualiza si el menú es tuyo, `main.lua:1119` es el manejador de `close-menu`): era
  nuestro propio cierre, atendido 21 ms más tarde. El módulo se quedaba con su navegación en pie y **sin menú en
  pantalla**: ⌫ saltaba de nivel y lo que cargaba no aparecía. El arreglo va en `mu.uosc`, así que lo heredan los
  catorce módulos, y es simétrico a `asking()` de ADR-112: se apunta el **cierre pedido y no confirmado**, y
  `open_type()` no devuelve un menú que ya está muerto, con el mismo seguro de un segundo por si uosc no está.
  Medido: de 9 de cada 10 a **20 de 20**. Y queda probado sin depender de la carrera: `tests/test_mu_uosc_cierre.py`
  comprueba el invariante con LuaJIT y un `mp` de pega —la primera prueba unitaria de Lua del proyecto—, dos de
  sus cuatro casos fallan con la versión de antes y los otros dos vigilan que el arreglo no se pase de largo.
  **(2) `last_notice` era un observable que pierde información.** «Ana se ha unido» llega y acto seguido llega
  «Enlaces copiados» —la sala ya sirve, que es asíncrono y con el equipo cargado se retrasa— y lo borra. Si las
  dos caen dentro de la misma ventana de muestreo (0,1 s), el primero **no se puede leer nunca**: un predicado
  que recuerde haberlo visto no basta, porque no sobrevive a ninguna muestra. Así que el dato cambia, no la
  espera: `mu-share` y `mu-feeds` publican los **ocho últimos avisos** en `notices`, que sí es un estado, y las
  doce esperas de los tests preguntan por el registro. Nueve de esas doce (en `test_share_http` y
  `test_share_guest`) tenían la misma fragilidad latente sin haber fallado todavía.
  **Lo que se aprende, y es la tercera vez.** N1, N3, N2 y estos dos: todos eran esperar un plazo o un valor
  instantáneo. Ante un fallo intermitente, el primer sitio donde mirar no es la carga ni el demonio —los dos se
  descartaron con medidas en ADR-121— sino qué instante se está leyendo y cómo convertirlo en un estado.
- ADR-123 · En Windows se le da a la persona un `.cmd`, y hace la cadena entera (H72).
  Ser lo pidió así: «necesito que la persona lo ejecute, y se haga todo de golpe». Había dos preguntas dentro.
  **(1) `.cmd` y no `.ps1`.** Un `.ps1` **no se ejecuta al hacer doble clic**: Windows lo abre en el editor. Y aunque
  se lance desde una consola, la directiva de ejecución lo bloquea, porque es un script sin firmar que viene de un
  zip descargado. El `.cmd` es un envoltorio de dos líneas que llama a PowerShell con `-NoProfile -ExecutionPolicy
  Bypass`, que vale **solo para ese proceso** y no cambia ningún ajuste del equipo. Lleva además `if errorlevel 1
  pause`, porque sin eso un fallo se lo lleva la ventana al cerrarse y la persona se queda sin saber qué pasó.
  **(2) Ni `.exe` ni `.msi`, mientras no haya firma.** Sin firmar, un ejecutable descargado se lleva el cartel azul
  de SmartScreen con el botón de ejecutar escondido detrás de «Más información»; un `.cmd` sacado de un zip, como
  mucho, el aviso pequeño con el botón de Ejecutar a la vista. Un `.msi` en `Program Files` pediría además
  administrador diciendo «Editor desconocido», cuando lo que hay ahora es por usuario y no pide nada. Y no aportaría
  nada que `tools/install.ps1` no haga ya (menú Inicio, asociaciones, enlaces `mpv-uos://`) salvo salir en «Agregar
  o quitar programas». Con certificado, el `.msi` sí es mejor y se hace: queda como decisión de Ser, que es quien
  paga y quien pone su nombre legal en el aviso de Windows.
  **Lo que hace el arranque, y el detalle que lo hace funcionar.** `bin/empezar.ps1` busca mpv (en el PATH y donde lo
  dejan winget, scoop, choco y los instaladores), lo instala con winget si falta, y **le pasa al lanzador la ruta
  exacta del `mpv.exe`** por `MPV_UOS_MPV`. Esto último no es un adorno: **una consola que acaba de instalar algo con
  winget NO ve el PATH nuevo**, porque el suyo es una copia hecha al arrancar. Mirar solo el PATH habría instalado
  mpv y fallado igual, que es el peor de los dos resultados; además se relee el PATH del registro antes de rendirse.
  **Y un fallo que apareció al hacerlo:** los `.ps1` que se reparten no llevaban BOM, y Windows PowerShell 5.1 —el
  que viene con Windows— lee un fichero sin BOM como ANSI, así que los acentos de los mensajes llegaban destrozados.
  Estaba justo en el aviso de «no encuentro mpv», el primero que ve alguien que acaba de descomprimir el zip. Los
  tres llevan BOM y hay un test que lo vigila.
  **El nombre del fichero va en ASCII** (`EMPEZAR-AQUI.cmd`, sin tilde): el explorador de Windows enseña mal los
  nombres con acentos de un zip si quien lo comprimió no marcó UTF-8, y el primer fichero que alguien ve no puede
  salir con un nombre roto.
- ADR-124 · Tres causas más de la batería, y una era un fallo del programa (H63/N2).
  Tres pasadas completas sobre ADR-123 dieron **1, 2 y 5 fallos**, siempre distintos. Cogidos uno a uno con el
  método que funciona —repetir el test solo en bucle hasta que caiga y leer el log de mpv—, salieron tres causas
  que no tienen nada que ver entre sí. Dos eran de los tests; la primera, no.
  **(1) La cuenta de «Continuar viendo» se infla (fallo del programa).** `test_mu_menu` daba `assert [3] == [2]`:
  una reproducción contada dos veces. H58 ya lo había arreglado haciendo que el identificador de la reproducción
  viajara en todas las llamadas, pero la memoria guardaba **solo el último identificador de cada vídeo**. Basta que
  entre dos llamadas de la reproducción B se cuele un guardado de posición rezagado de la A —y el reproductor
  guarda la posición cada 15 s y reintenta lo que no se contesta a tiempo— para que la memoria se pise y B vuelva a
  contar. No es cosa de los tests: con el equipo cargado, al usuario se le infla la cuenta. Ahora la idempotencia
  es un **estado del almacén**, no una memoria: una tabla `plays(key, play_id)` con clave primaria, así que «una
  reproducción se cuenta una vez» lo garantiza SQLite, sobrevive a reiniciar el demonio y se poda a los dieciséis
  últimos identificadores por vídeo (es un seguro, no un historial). El test nuevo reproduce el entrelazado sin
  depender de ninguna carrera y falla con la versión de antes: `assert 3 == 2`.
  **(2) Un cambio hecho en los primeros milisegundos no se puede distinguir del valor de partida.** Tres tests de
  `test_prefs` fallaban **1 de cada 3** ejecutándolos solos. El log lo dijo: `volume` y `speed` se cambiaban a los
  132 ms y nunca se guardaban, y `sub-scale` a los 137 ms sí. Causa: mpv **junta** el primer aviso del observador
  con un cambio que llegue en el mismo instante, y `mu-prefs` descarta ese primer aviso **a propósito** —lo que
  venga de tu `mpv.conf` o de la línea de órdenes no es una elección tuya de ahora—. El programa tenía razón y el
  test estaba mal, pero el programa tampoco daba forma de saberlo: ahora publica `watching`, que es cuándo cuenta
  de verdad un cambio, contando cuántas propiedades han dado ya su primer aviso (con un seguro de 1 s por si alguna
  no existe en ese mpv). El test espera por ese estado. De 1 de cada 3 a 11 de 12 —lo que quedaba era (3)—.
  **(3) Un nombre de socket que se podía leer como un PID.** `bin/mpv-uos` limpia los sockets huérfanos porque mpv
  no borra el suyo al salir, y decide leyendo el nombre: `mpv-<pid>.sock`, y solo toca los que son **todos
  dígitos**. El arnés nombraba los suyos con 8 hexadecimales al azar, que salen todo numéricos el **2,3 %** de las
  veces ((10/16)^8): cuando eso pasaba, el lanzador de la segunda instancia le borraba el socket a la primera, que
  estaba viva, y el test se quedaba 15 s esperando un socket que ya no existía. Observado 1 de cada 23, que para el
  tamaño de la muestra es exactamente eso. El tag lleva ahora una letra delante, hay un test que lo vigila y el
  lanzador dice en un comentario que ese nombre ES el pid, para quien cree sockets ahí en el futuro. 40 de 40.
  **Lo que esto dice del método.** Las tres salieron de lo mismo: reproducir el fallo solo, en bucle, y leer el log
  —no razonar sobre el código—. Y la primera recuerda por qué merece la pena: un «test frágil» puede ser un fallo
  del programa esperando a que alguien lo mire.
  **Dos más de la misma tanda (2026-10-05, tarde).** `test_mu_modes` fallaba por **dos** carreras: esperaba
  «view == root» y leía unas filas que se publican después —desde N1 se vacían al cambiar de vista, así que leer a
  medias es leer `[]`—, y esperaba a que **un** script dijera que ya no está en modo sencillo para exigir a los
  veinte haber devuelto sus botones, cuando cada uno observa el modo por su cuenta y no cambian a la vez. Las dos
  esperan ahora por el estado completo, y eso convierte el test en la prueba del invariante que importa: **salir del
  modo sencillo devuelve TODOS los botones**. De 4 de 5 a 20 de 20, y esas veinte alcanzaron siempre el estado
  completo, así que el invariante del programa se cumple. Y `test_mu_iptv_live` pedía un salto en cuanto había
  `time-pos`, que **no** quiere decir que se pueda saltar: en un HLS el demuxer puede no tener aún el rango y mpv
  contesta «error running command». Ahora se espera por `seekable`, que es el estado que lo dice.
  **Y uno que NO se ha explicado, dicho para que nadie repita el trabajo.**
  `test_integration_mpvd::test_mu_core_starts_daemon_registers_and_round_trips` falló una vez esperando que un
  trabajo de la sesión pasara a «cancelado» en 10 s. **No se reproduce aislado: 12 de 12.** Se buscó un hueco en el
  camino del cancelado y no lo hay: `cancel()` no toca un trabajo que ya no esté en cola o corriendo, el obrero
  marca el trabajo que de verdad está ejecutando, y `cancel_session` lee una foto consistente porque corre entre
  dos `await`. La hipótesis que queda —que con la batería entera por delante la transición asíncrona no entra en
  los 10 s— **no está comprobada**; no se ha tocado nada por no arreglar lo que no se ha demostrado roto.
- ADR-125 · «¿Es mío el menú?» es la pregunta que hay que hacer antes de tirar trabajo (H63/N2).
  Con la máquina ya con memoria, tres pasadas completas dieron **0, 0 y 1 fallo**: `test_recap`, esperando 60 s a
  que el resumen llegara a «hecho». Aislado pasa **10 de 10**, así que el log de la pasada completa era la única
  fuente, y ahí estaba: `mu-recap`, al recibir la respuesta de mpvd, hacía
  `if uosc.open_type() ~= MENU then state.status = 'idle'; return end` —o sea, **si en ese instante el menú abierto
  no era el suyo, tiraba el resumen**—. Y ese instante es el hueco de ADR-112: entre pedirle el menú a uosc y verlo
  confirmado pasan de 1 a 27 ms con el equipo ocupado. No es un test frágil: **pides «¿Qué me he perdido?» y no
  sale nada**, y cuanto más cargado está el equipo, más fácil.
  **La cura es un primitivo, no un parche.** `mu.uosc` expone `mine(tipo)` = «el menú abierto es mío **o** lo he
  pedido y uosc aún no lo ha confirmado», con el mismo seguro de un segundo que `asking()`. Esa es la pregunta
  correcta antes de **descartar** algo; para refrescar lo que ya está en pantalla sigue bastando `open_type()`,
  porque perder un refresco se arregla en el siguiente.
  **Dónde se ha aplicado y dónde NO.** Hay una docena de sitios que leen `open_type()`, y la mayoría solo deciden
  si refrescan: ahí no se ha tocado nada, porque cambiarlo sería ruido. Se ha cambiado donde se **pierde trabajo**:
  los dos caminos de `mu-recap` (el resumen y la prosa) y el sondeo de `mu-cast`, que paraba la lista de aparatos
  por un milisegundo en el que el menú no era suyo.
  **Lo que las pruebas pueden y no pueden demostrar.** El primitivo queda cerrado con cuatro casos deterministas en
  la prueba unitaria de Lua (el hueco, el menú de otro, el seguro de un segundo y «si lo cierro yo, deja de ser
  mío»). El fallo que lo levantó es 1 de cada 3 pasadas completas, así que **no** hay un test que lo reproduzca a
  voluntad: lo honesto es decir que el mecanismo está demostrado y que la frecuencia se verá en las siguientes
  tandas.
- ADR-126 · Lo que encontró el guardián de logs: dos fallos del programa en la primera tanda (H63/N2).
  La tanda sobre `a5d1dfd` dio 0, 2 y 2 fallos, y los logs guardados en `tmp/fallos/` convirtieron dos de ellos en
  causas en diez minutos, que es exactamente para lo que se hizo.
  **(1) Una respuesta tardía te devolvía a la vista de la que acababas de salir.** El rastro, con marcas de tiempo:
  `2.754 menu/type="mu-library"` · `2.970` se pulsa ⌫ · `2.975 menu/type="mu-menu"` (bien, el menú principal) ·
  `3.018` llega la respuesta de una petición que la biblioteca había hecho antes · `3.019 open-menu` ·
  `3.020 menu/type="mu-library"`. O sea: **pulsas «Atrás», llegas al menú principal y el menú que acabas de dejar
  te salta encima 40 ms después**. La causa está en una forma repetida en **trece** módulos: `show()` elegía entre
  `update-menu` y `open-menu` mirando si el menú de pantalla era el suyo, y la rama `else` **abría** —también
  cuando ya te habías ido—. El criterio bueno no era `force_open` (`mu-av` no lo usa nunca y se apoya solo en esa
  rama) sino **la pila**: `open_view` mete la vista en `state.stack` antes de pintar, así que una llamada legítima
  siempre tiene pila y una respuesta tardía después de volver atrás la tiene vacía. Ahora: se actualiza si el menú
  es nuestro (`mine`, que cuenta el pedido y no confirmado), se abre solo si queda pila, y si no, no se toca uosc.
  Trece módulos y `mu-share`, que tenía la misma forma con otra firma. De paso arregla el caso hermano: cerrar el
  menú con Esc y que una respuesta tardía lo reabriera.
  **(2) mpv no acepta un SRT vacío, y el demonio escribía uno.** `test_mu_subs` falló en dos pasadas, y el log de
  mpv lo dijo: `[lavf] No format found` y `[e] Can not open external file …/base.es.srt`. En el código estaba
  escrito con todas las letras: «always (re)write the SRT so mpv can sub-add it right away, **even when empty**».
  Medido con mpv de verdad: un SRT vacío **lo rechaza** (`sub-add` falla y no hay pista); uno con un solo rótulo en
  blanco lo acepta. Así que esa intención —que la pista exista desde el principio y se vaya llenando— **nunca
  funcionó**: la pista no aparecía hasta los primeros segmentos y el error se quedaba en un log que nadie mira.
  Ahora, sin segmentos se escribe un rótulo en blanco de medio segundo (válido, invisible, se sustituye en cuanto
  hay texto) y ese camino escribe con el **mismo rename atómico** que el otro, que ya lo hacía: media línea leída
  es un fichero que mpv tampoco abre.
  **(3) Y el tercero, que sigue sin explicar:** `test_appimage` se quedó sin socket en 15 s. El AppImage tiene que
  montarse antes de arrancar y la pasada iba a 32 minutos en vez de 20, así que la sospecha es la carga; **no está
  comprobado** y no se ha tocado nada.
- ADR-127 · Un umbral de rendimiento no va en la batería de corrección (H63/N2).
  Las tres pasadas sobre ADR-126 dieron **1.028 pasando y el mismo fallo las tres**:
  `test_asr_engine`, `assert res.rtf < 3.0`. No era intermitencia ni una regresión: whisper tardó **3,89** veces la
  duración del audio en vez de menos de 3, con la máquina a carga 4,9 —el propio check más el escritorio— y pasadas
  de 28-32 minutos en vez de 20. Con la máquina tranquila el mismo test da **1,40**.
  **Por qué se cambia el test y no el umbral.** Un umbral así **mide el equipo, no el código**: es la misma
  enfermedad de ADR-121 en versión rendimiento —un plazo es una apuesta sobre lo rápido que va la máquina—. Y hay un
  precedente en este proyecto que lo zanja: los once fallos de H55 se colaron porque **una batería que falla al azar
  es una batería que se deja de leer**. Una que se pone roja porque tienes el navegador abierto se deja de leer
  igual. Subir el número a 6 habría sido la misma apuesta, más grande.
  **Qué queda.** Un tope de **patología** (`rtf < 15`: lo peor medido con carga es 3,9, así que solo salta si algo se
  ha roto de verdad —el modelo mal cargado, un hilo en vez de cuatro—), el dato exacto impreso para cuando falle, y
  el umbral fino disponible a mano con la máquina en reposo (`MU_BENCH=1`). Las comprobaciones de que la
  transcripción es CORRECTA —las palabras clave, el idioma, los límites de los segmentos, una sola ejecución de
  whisper— siguen todas en la batería de siempre.
  **Y el dato que importa de esa tanda, que el rojo tapaba:** en las tres pasadas **no falló nada funcional**. Cero.

- ADR-128 · El código se publica, y por qué el repositorio lleva sufijo y `main` no se toca (H50).
  **Qué se decide.** El repositorio se hace público y la página del proyecto enseña el código, en vez de un
  escaparate sin descarga. Con ello: `LICENSE` (MIT) en la raíz —hasta hoy la licencia solo vivía en
  `pyproject.toml`, que es declararla sin concederla— y la atribución de los datos de SponsorBlock dentro del
  programa, en el menú de Ayuda, porque su licencia (CC BY-NC-SA 4.0) la exige donde se usan los datos y una
  atribución que hay que ir a buscar a la web no es una atribución.
  **El nombre no se decidía aquí.** El guion de la página se escribió llamando al programa «Alalaya Player» y
  preguntando si el nombre chocaba con el proyecto OSINT Atalaya. Ya estaba resuelto: ADR-084 eligió «Atalaya
  Player» y `brand.json` fija `https://solucionesconscientes.es/atalaya`, dirección que la propia aplicación
  enseña en Ayuda y en Preferencias. Publicarla en otra URL habría dejado mintiendo al programa. Esto es el coste
  de no leer las decisiones vigentes antes de escribir: una página entera con el nombre equivocado.
  **Por qué el repositorio es `atalaya-player` y no `atalaya`.** `solucionesconscientes/atalaya` es del proyecto
  OSINT Atalaya, que queda aparcado pero ya ocupa el hueco. La URL pública del programa sí es `/atalaya`, porque esa
  la decide `brand.json`, no GitHub.
  **Por qué `main` se queda con el andamiaje.** La regla del proyecto es no fusionar a `main`, y publicar no es
  motivo para romperla por mi cuenta: la rama por defecto del repositorio público es el tronco de trabajo
  (`nocturno/2026-09-28`), que lleva todo el código, y `main` sigue donde estaba. Pasar el tronco a `main` es un
  `git push origin nocturno/2026-09-28:main` cuando Ser quiera, y es suyo decidirlo.
- ADR-129 · La tercera vez que una bandera se lee en el instante equivocado (H63/N2).
  Tres pasadas sobre el árbol con el repositorio ya publicado: la primera dio **3 fallos y 1.026 pasando**, las dos
  siguientes **1.029 y 1.029, limpias**. De los tres, dos eran esperas que se agotaron —`test_appimage` (el socket
  de mpv) y `test_flujos_e2e` (el tipo de menú)—, de las que ya hablan ADR-121 y ADR-124. El tercero no era un
  plazo: `assert True is False` en `test_mu_av.py:219`.
  **Qué pasaba.** Al restaurar la ventana, el test espera a que vuelva el vídeo y a que vuelva el título, y entonces
  lee `minimized_audio` de una sentada. Pero el título lo restaura mpv y la bandera la publica mu-av en su propio
  tic: son dos avisos distintos del mismo gesto, y esperar al primero no garantiza el segundo. Por eso falla una vez
  de cada tres y nunca dos seguidas. Es exactamente ADR-121 —esperar por un estado y no por un instante— en el único
  sitio donde todavía quedaba un `assert` de lectura directa detrás de una espera.
  **Qué NO era.** No es un fallo del programa: si la bandera no se limpiara, fallaría siempre, y pasa dos de cada
  tres veces. Y tampoco es carga: el `assert` no tiene plazo que agotar.
  **Lo que deja la noche, además:** una tanda con 4 fallos que resultaron ser CERO —corrían dos baterías a la vez,
  los cuatro pasaron aislados—. Una batería compartiendo máquina con otra no mide el código, mide el reparto de CPU,
  y su rojo no se cree hasta repetirlo solo.
- ADR-130 · Arranca limpio: nada se enciende por nuestra cuenta (H63, a raíz de unos tirones de Ser).
  **Qué pasó.** Ser vio tirones con una serie 1080p HEVC 10 bits y, al abrir el mismo archivo con mpv a secas,
  perfecto. Medido a pantalla completa, un minuto estable y alternando: **180 fotogramas perdidos por minuto con
  sus preferencias y 0 sin ellas**. La causa era un filtro de vídeo que él mismo había encendido en el menú,
  «Protección fotosensible» (`photosensitivity=frames=30`), que analiza treinta fotogramas por delante **en la
  CPU** sobre cada fotograma de 1080p, en un equipo que ya gasta 1,3 de sus 4 núcleos descodificando ese HEVC
  porque esta GPU no tiene HEVC 10 bits por hardware.
  **Por qué es culpa nuestra igualmente.** El filtro lo encendió él, pero el programa se lo dejó encendido
  semanas, no dijo nada mientras la imagen se rompía, y nada en pantalla relacionaba una cosa con la otra. Un
  programa que va peor que el motor que lleva dentro y no lo dice no tiene razón de existir.
  **La regla, que es de Ser:** el reproductor **arranca exactamente como mpv** y lo que cueste algo lo enciende
  quien lo quiera. Apagado por defecto desde hoy: la detección de intro (`mu-intro.enabled`, que analizaba cada
  archivo local al abrirlo), SponsorBlock (`mu-intro.sponsorblock`, que salía a la red por cada vídeo de
  YouTube), y el pre-subtitulado del siguiente episodio (`mu-subs.precompute_next`, que transcribía con whisper
  mientras veías el actual). Y fuera `vo=gpu-next` del mpv.conf: fijar un renderizador es decidir por el usuario
  algo que mpv ya decide, y cuando mpv cambie nos quedaríamos con lo viejo.
  **La única excepción, y con medida:** `hwdec=auto-safe` se queda. En un H.264 1080p de este equipo gasta **5 %
  de un núcleo contra 35 % sin ella**, dos medidas de cada; y en lo que la GPU no sabe cae sola a software sin
  penalización. No cuesta nada y ahorra batería: eso no es decidir por el usuario, es no hacerle pagar de más.
  **En los tests.** Los seis que daban por hecho que esto venía encendido ahora lo encienden ellos
  (`--script-opts=mu-intro-enabled=yes`…). Un test que depende de un valor por defecto se rompe cuando el valor
  cambia, que es justo lo que ha pasado y por lo que ahora lo dicen.
  **Lo que falta y no entra aquí:** que el programa avise cuando un filtro que has encendido no cabe en tu
  máquina. Se mide ya (`frame-drop-count`) y mu-av tiene vista de diagnóstico; lo que falta es que no espere a
  que la abras.
