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
