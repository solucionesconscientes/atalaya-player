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
