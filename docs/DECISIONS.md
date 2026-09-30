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
- ADR-038 · Traducción con dos motores y SRT guardables: OPUS-MT tc-big (Helsinki-NLP, CC-BY 4.0) para es/ca↔en, descargado bajo
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

