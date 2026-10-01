# yt-dlp — verificación contra la fuente real (2026-09-28)

Documento de investigación para el hito H3 (yt-dlp avanzado). Todo lo que sigue se ha comprobado
ejecutando comandos en esta máquina (`pc-latitude5480`, x86_64, 4 núcleos, Linux 7.0) y leyendo
el README oficial (`https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/README.md`), la wiki EJS
(`https://github.com/yt-dlp/yt-dlp/wiki/EJS`) y la API de releases de GitHub. Nada está copiado de memoria.

## 1. Estado del sistema

| Componente | Valor |
|---|---|
| `which yt-dlp` | `/usr/local/bin/yt-dlp` |
| `yt-dlp --version` (sistema) | `2026.08.19` (coincide con la última release estable) |
| `mpv --version` | `mpv v0.41.0`, libplacebo v7.360.0 |
| `python3` del sistema | 3.14.4 |
| `.venv/bin/python` (uv) | 3.12.14 |
| ffmpeg / ffprobe | 8.0.1 (`/usr/bin/ffmpeg`) |
| deno | **no instalado** |
| node | `/usr/bin/node` v22.23.2 (>= 22.0.0 mínimo que exige yt-dlp) |
| bun / qjs (QuickJS) | no instalados |

## 2. Última release oficial (GitHub)

`curl -s https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest`

- **tag**: `2026.08.19` · nombre "yt-dlp 2026.08.19" · publicada `2026-08-19T23:48:43Z`
- Base de descarga: `https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/<asset>`
- Sumas: `SHA2-256SUMS` (1595 B) y `SHA2-512SUMS`, ambas con firma `.sig` (GPG). También `_update_spec`.

### Assets Linux
| Asset | Tamaño | Qué es |
|---|---|---|
| `yt-dlp` | 3.07 MB | **zipimport**: fichero zip con shebang `#!/usr/bin/env python3`. Necesita un Python 3.10+ del sistema. Incluye `yt_dlp_ejs` (solver JS de YouTube) dentro del zip. NO incluye `curl_cffi` (impersonación). |
| `yt-dlp_linux` | 40.4 MB | Binario PyInstaller autónomo (glibc, x86_64). Lleva Python, certifi, brotli, websockets, requests, curl_cffi, mutagen, pycryptodomex, yt-dlp-ejs. |
| `yt-dlp_linux.zip` | 40.5 MB | El mismo binario PyInstaller en zip |
| `yt-dlp_linux_aarch64` / `.zip` | 40.2 MB | PyInstaller ARM64 glibc |
| `yt-dlp_linux_armv7l.zip` | 38.7 MB | PyInstaller ARMv7 |
| `yt-dlp_musllinux` / `_aarch64` (+ `.zip`) | ~40 MB | PyInstaller para musl (Alpine) |
| `yt-dlp.tar.gz` | 6.0 MB | Código fuente |

### Assets Windows y macOS (para docs/PLATAFORMAS.md)
| Plataforma | Asset | Tamaño |
|---|---|---|
| Windows x64 | `yt-dlp.exe` / `yt-dlp_win.zip` | 17.8 / 18.1 MB |
| Windows x86 (32 bits) | `yt-dlp_x86.exe` / `yt-dlp_win_x86.zip` | 13.2 / 13.3 MB (sin curl_cffi) |
| Windows ARM64 | `yt-dlp_arm64.exe` / `yt-dlp_win_arm64.zip` | 21.2 / 21.5 MB |
| macOS (universal2, 10.15+) | `yt-dlp_macos` / `yt-dlp_macos.zip` | 37.1 / 53.9 MB |

Nota del README: Windows necesita el "Microsoft Visual C++ 2010 SP1 Redistributable (x86)" si el exe se queja de `MSVCR100.dll`.

### Diferencia zipimport vs PyInstaller
- `yt-dlp` (zipimport): pequeño, multiplataforma Unix, se ejecuta con el `python3` que haya en PATH (o con el que le pasemos:
  `python3 vendor/dl/yt-dlp ...`). Depende de la versión de Python del sistema (3.10+). Sin curl_cffi.
  Ideal para el proyecto porque ya tenemos un Python 3.12 en `.venv` y así el hash del artefacto es el mismo en todas las máquinas Unix.
- `yt-dlp_linux` (PyInstaller): 40 MB, sin dependencia de Python, pero atado a glibc/x86_64 y con arranque más lento (descomprime en /tmp).

## 3. Descarga y verificación (hecha)

```
vendor/dl/yt-dlp            3072469 bytes
vendor/dl/SHA2-256SUMS      (descargado de la release)
sha256sum vendor/dl/yt-dlp  = 1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6   -> "yt-dlp: OK" con sha256sum -c
```
SHA-256 de otros assets de la misma release (de SHA2-256SUMS), por si se vendoriza otro:
- `yt-dlp_linux` = `58162f9bfdc27458ea47bfcb311cf47028f17d8154a8bf7d689861d46399230a`
- `yt-dlp_linux_aarch64` = `b16e4dab368a816cd05d477d698a605a6ae87ccee1c8ffd38fa21d7254141fcc`
- `yt-dlp.exe` = `66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a`
- `yt-dlp_macos` = `0f192b7ec147ab6288885d6351d9ab67367640029b4377576ef46dd79cf7b202`

Ejecución comprobada:
- `python3 vendor/dl/yt-dlp --version` -> `2026.08.19` (Python 3.14.4 del sistema)
- `.venv/bin/python vendor/dl/yt-dlp --version` -> `2026.08.19` (Python 3.12.14 de uv) **<- recomendado para mpvd**
- `chmod +x vendor/dl/yt-dlp && ./vendor/dl/yt-dlp --version` -> `2026.08.19` (shebang `#!/usr/bin/env python3`)

Propuesta para `vendor.lock`:
```
YTDLP_VERSION=2026.08.19
YTDLP_URL=https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp
YTDLP_SHA256=1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6
```
Aviso: `-U`/`--update` sobre el zipimport reescribe el propio fichero (se saltaría el lock). Para el proyecto es mejor
comprobar `releases/latest` como máximo una vez al día desde mpvd y re-descargar + verificar SHA, o pasar siempre `--no-update`.

## 4. Runtime JavaScript para YouTube (EJS)

Fuente: README "DEPENDENCIES" + wiki EJS. Resumen verificado:

- Desde 2025 YouTube exige resolver "challenges" JS (n/sig). yt-dlp lo hace con los scripts **yt-dlp-ejs** (van dentro
  del zipimport y de los binarios PyInstaller: "No additional action required") ejecutados por un **runtime JS externo**.
- Runtimes admitidos (orden de prioridad): **deno** (recomendado, único habilitado por defecto, mín. 2.3.0), **node** (mín. 22.0.0),
  **quickjs / quickjs-ng** (el ejecutable debe llamarse `qjs`), **bun** (deprecado; >1.3.14 no soportado).
- README: "YouTube extraction without a JS runtime has been deprecated, and some formats may be missing."
  Sin runtime, el cliente `web` se omite (README, `player_client`) y se usa `visionos`.
- Opciones CLI (línea de ayuda exacta más abajo, sección 5):
  - `--js-runtimes RUNTIME[:PATH]` — p. ej. `--js-runtimes node`, `--js-runtimes deno:/ruta/a/deno` o `--js-runtimes deno:/ruta/dir/`.
    Se puede repetir. Para forzar uno de menor prioridad habiendo deno: `--no-js-runtimes --js-runtimes node`.
  - `--no-js-runtimes` — limpia la lista (incluido el deno por defecto).
  - `--remote-components COMPONENT` — valores `ejs:npm` (solo deno/bun) o `ejs:github`. Por defecto NADA remoto.
    **No hace falta** con el zipimport/binario oficial porque yt-dlp-ejs va incluido.
  - `--no-remote-components` — prohíbe cualquier descarga de componentes.
  - `--extractor-args "youtube-ejs:jitless=true"` — modo sin JIT (más seguro, más lento) para deno/node/bun.
  - `--extractor-args "youtube:jsc_trace=true"` — traza del JS challenge para depurar.
- Seguridad (wiki): deno ejecuta con permisos restringidos (sin FS ni red); node "algunos permisos restringidos";
  bun sin restricciones.

### Resultado real en esta máquina hoy
- **Sin runtime** (`[debug] JS runtimes: none`): `-J` de `aqz-KE-bpKQ` **funciona** (53 formatos, hasta 2160p60, `format_id` elegido `401+251`) con este aviso:
  ```
  WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; to use another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config. YouTube extraction without a JS runtime has been deprecated, and some formats may be missing. See  https://github.com/yt-dlp/yt-dlp/wiki/EJS  for details on installing one
  ```
  Y la **descarga real también funciona** sin runtime (cliente `visionos`): `-f 139` (3.69 MiB) y `-f 160+139` (merge ffmpeg, 8.4 MB) OK.
- **Con `--js-runtimes node`** (`[debug] JS runtimes: node-22.23.2`): `-J` funciona igual (53 formatos). Al descargar aparece
  `Some web client https formats have been skipped as they are missing a URL. YouTube is forcing SABR streaming for this client. See https://github.com/yt-dlp/yt-dlp/issues/12482` — es informativo; los formatos de `visionos` siguen descargables.
- `-f "worst[filesize<5M]/worst"` **falla** con y sin runtime: `ERROR: [youtube] aqz-KE-bpKQ: Requested format is not available`.
  Causa: YouTube ya no ofrece ningún formato combinado (vídeo+audio en un solo fichero); `worst`/`best` a secas exigen combinado.
  Usar siempre selectores con `*` o con `+`: `-f "wv*+wa/w"`, `-f "bv*+ba/b"`, o ids explícitos (`160+139`).
- Conclusión: hoy YouTube funciona **sin** runtime en esta máquina, pero está deprecado y puede romperse en cualquier momento.
  Plan: habilitar `node` si existe (`--js-runtimes node`) y vendorizar `deno` en `vendor/bin` como opción robusta (ver abajo).

### deno estático (NO descargado todavía)
- Última release: `v2.9.7` (2026-09-17). Wiki: descargar `deno`, **no** `denort`.
- Linux x86_64: `https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-unknown-linux-gnu.zip` (41.6 MB; el zip contiene un único binario `deno`, ~150 MB descomprimido).
  SHA: `https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-unknown-linux-gnu.zip.sha256sum`
- Linux aarch64: `deno-aarch64-unknown-linux-gnu.zip` (39.8 MB) · macOS x86_64: `deno-x86_64-apple-darwin.zip` (42.3 MB) · macOS arm64: `deno-aarch64-apple-darwin.zip` (38.5 MB) · Windows x64: `deno-x86_64-pc-windows-msvc.zip` (42.6 MB).
- Uso: `--js-runtimes deno:vendor/bin/deno` (ruta al binario o a su carpeta).

## 5. Opciones verificadas en `yt-dlp --help` (2026.08.19)

Todas las opciones pedidas **existen**. `--ppa` existe como alias de `--postprocessor-args` (aparece en su texto: "(Alias: --ppa)").
`--help` y `--verbose --help` listan las mismas 255 opciones (solo cambian 3 líneas de cabecera).

```
    -J, --dump-single-json          Quiet, but print JSON information for each
                                    URL or infojson passed. Simulate unless
                                    --no-simulate is used. If the URL refers to
                                    a playlist, the whole playlist information
                                    is dumped in a single line
    --flat-playlist                 Do not extract a playlist's URL result
                                    entries; some entry metadata may be missing
                                    and downloading may be bypassed
    --no-playlist                   Download only the video, if the URL refers
                                    to a video and a playlist
    --yes-playlist                  Download the playlist, if the URL refers to
                                    a video and a playlist
    -f, --format FORMAT             Video format code, see "FORMAT SELECTION"
                                    for more details
    -S, --format-sort SORTORDER     Sort the formats by the fields given, see
                                    "Sorting Formats" for more details
    --merge-output-format FORMAT    Containers that may be used when merging
                                    formats, separated by "/", e.g. "mp4/mkv".
                                    Ignored if no merge is required. (currently
                                    supported: avi, flv, mkv, mov, mp4, webm)
    --remux-video FORMAT            Remux the video into another container if
                                    necessary (currently supported: avi, flv,
                                    gif, mkv, mov, mp4, webm, aac, aiff, alac,
                                    flac, m4a, mka, mp3, ogg, opus, vorbis,
                                    wav). If the target container does not
                                    support the video/audio codec, remuxing will
                                    fail. You can specify multiple rules; e.g.
                                    "aac>m4a/mov>mp4/mkv" will remux aac to m4a,
                                    mov to mp4 and anything else to mkv
    --recode-video FORMAT           Re-encode the video into another format if
                                    necessary. The syntax and supported formats
                                    are the same as --remux-video
    -x, --extract-audio             Convert video files to audio-only files
                                    (requires ffmpeg and ffprobe)
    --audio-format FORMAT           Format to convert the audio to when -x is
                                    used. (currently supported: best (default),
                                    aac, alac, flac, m4a, mp3, opus, vorbis,
                                    wav). You can specify multiple rules using
                                    similar syntax as --remux-video
    --audio-quality QUALITY         Specify ffmpeg audio quality to use when
                                    converting the audio with -x. Insert a value
                                    between 0 (best) and 10 (worst) for VBR or a
                                    specific bitrate like 128K (default 5)
    --embed-subs                    Embed subtitles in the video (only for mp4,
                                    webm and mkv videos)
    --write-subs                    Write subtitle file
    --write-auto-subs               Write automatically generated subtitle file
                                    (Alias: --write-automatic-subs)
    --sub-langs LANGS               Languages of the subtitles to download (can
                                    be regex) or "all" separated by commas, e.g.
                                    --sub-langs "en.*,ja" (where "en.*" is a
                                    regex pattern that matches "en" followed by
                                    0 or more of any character). You can prefix
                                    the language code with a "-" to exclude it
                                    from the requested languages, e.g. --sub-
                                    langs all,-live_chat. Use --list-subs for a
                                    list of available language tags
    --sub-format FORMAT             Subtitle format; accepts formats preference
                                    separated by "/", e.g. "srt" or
                                    "ass/srt/best"
    --convert-subs FORMAT           Convert the subtitles to another format
                                    (currently supported: ass, lrc, srt, vtt).
                                    Use "--convert-subs none" to disable
                                    conversion (default) (Alias: --convert-
                                    subtitles)
    --embed-thumbnail               Embed thumbnail in the video as cover art
    --write-thumbnail               Write thumbnail image to disk
    --embed-chapters                Add chapter markers to the video file
                                    (Alias: --add-chapters)
    --embed-metadata                Embed metadata to the video file. Also
                                    embeds chapters/infojson if present unless
                                    --no-embed-chapters/--no-embed-info-json are
                                    used (Alias: --add-metadata)
    --embed-info-json               Embed the infojson as an attachment to
                                    mkv/mka video files
    --sponsorblock-mark CATS        SponsorBlock categories to create chapters
                                    for, separated by commas. Available
                                    categories are sponsor, intro, outro,
                                    selfpromo, preview, filler, interaction,
                                    music_offtopic, hook, poi_highlight,
                                    chapter, all and default (=all). You can
                                    prefix the category with a "-" to exclude
                                    it. See [1] for descriptions of the
                                    categories. E.g. --sponsorblock-mark
                                    all,-preview [1] https://wiki.sponsor.ajay.a
                                    pp/w/Segment_Categories
    --sponsorblock-remove CATS      SponsorBlock categories to be removed from
                                    the video file, separated by commas. If a
                                    category is present in both mark and remove,
                                    remove takes precedence. The syntax and
                                    available categories are the same as for
                                    --sponsorblock-mark except that "default"
                                    refers to "all,-filler" and poi_highlight,
                                    chapter are not available
    --sponsorblock-chapter-title TEMPLATE
                                    An output template for the title of the
                                    SponsorBlock chapters created by
                                    --sponsorblock-mark. The only available
                                    fields are start_time, end_time, category,
                                    categories, name, category_names. Defaults
                                    to "[SponsorBlock]: %(category_names)l"
    --no-sponsorblock               Disable both --sponsorblock-mark and
                                    --sponsorblock-remove
    --sponsorblock-api URL          SponsorBlock API location, defaults to
                                    https://sponsor.ajay.app
    -o, --output [TYPES:]TEMPLATE   Output filename template; see "OUTPUT
                                    TEMPLATE" for details
    -P, --paths [TYPES:]PATH        The paths where the files should be
                                    downloaded. Specify the type of file and the
                                    path separated by a colon ":". All the same
                                    TYPES as --output are supported.
                                    Additionally, you can also provide "home"
                                    (default) and "temp" paths. All intermediary
                                    files are first downloaded to the temp path
                                    and then the final files are moved over to
                                    the home path after download is finished.
                                    This option is ignored if --output is an
                                    absolute path
    --newline                       Output progress bar as new lines
    --progress-template [TYPES:]TEMPLATE
                                    Template for progress outputs, optionally
                                    prefixed with one of "download:" (default),
                                    "download-title:" (the console title),
                                    "postprocess:",  or "postprocess-title:".
                                    The video's fields are accessible under the
                                    "info" key and the progress attributes are
                                    accessible under "progress" key. E.g.
                                    --console-title --progress-template
                                    "download-
                                    title:%(info.id)s-%(progress.eta)s"
    --no-progress                   Do not print progress bar
    --progress                      Show progress bar, even if in quiet mode
    -O, --print [WHEN:]TEMPLATE     Field name or output template to print to
                                    screen, optionally prefixed with when to
                                    print it, separated by a ":". Supported
                                    values of "WHEN" are the same as that of
                                    --use-postprocessor (default: video).
                                    Implies --quiet. Implies --simulate unless
                                    --no-simulate or later stages of WHEN are
                                    used. This option can be used multiple times
    --print-to-file [WHEN:]TEMPLATE FILE
                                    Append given template to the file. The
                                    values of WHEN and TEMPLATE are the same as
                                    that of --print. FILE uses the same syntax
                                    as the output template. This option can be
                                    used multiple times
    -w, --no-overwrites             Do not overwrite any files
    -c, --continue                  Resume partially downloaded files/fragments
                                    (default)
    --no-part                       Do not use .part files - write directly into
                                    output file
    -N, --concurrent-fragments N    Number of fragments of a dash/hlsnative
                                    video that should be downloaded concurrently
                                    (default is 1)
    -r, --limit-rate RATE           Maximum download rate in bytes per second,
                                    e.g. 50K or 4.2M
    -R, --retries RETRIES           Number of retries (default is 10), or
                                    "infinite"
    --socket-timeout SECONDS        Time to wait before giving up, in seconds
    -U, --update                    Update this program to the latest stable
                                    version
    --update-to [CHANNEL]@[TAG]     Upgrade/downgrade to a specific version.
                                    CHANNEL can be a repository as well. CHANNEL
                                    and TAG default to "stable" and "latest"
                                    respectively if omitted; See "UPDATE" for
                                    details. Supported channels: stable,
                                    nightly, master
    --no-update                     Do not check for updates (default)
    --js-runtimes RUNTIME[:PATH]    Additional JavaScript runtime to enable,
                                    with an optional location for the runtime
                                    (either the path to the binary or its
                                    containing directory). This option can be
                                    used multiple times to enable multiple
                                    runtimes. Supported runtimes are (in order
                                    of priority, from highest to lowest): deno,
                                    node, quickjs, bun. Only "deno" is enabled
                                    by default. The highest priority runtime
                                    that is both enabled and available will be
                                    used. In order to use a lower priority
                                    runtime when "deno" is available, --no-js-
                                    runtimes needs to be passed before enabling
                                    other runtimes
    --no-js-runtimes                Clear JavaScript runtimes to enable,
                                    including defaults and those provided by
                                    previous --js-runtimes
    --remote-components COMPONENT   Remote components to allow yt-dlp to fetch
                                    when required. This option is currently not
                                    needed if you are using an official
                                    executable or have the requisite version of
                                    the yt-dlp-ejs package installed. You can
                                    use this option multiple times to allow
                                    multiple components. Supported values:
                                    ejs:npm (external JavaScript components from
                                    npm), ejs:github (external JavaScript
                                    components from yt-dlp-ejs GitHub). By
                                    default, no remote components are allowed
    --no-remote-components          Disallow fetching of all remote components,
                                    including any previously allowed by
                                    --remote-components or defaults.
    --cookies-from-browser BROWSER[+KEYRING][:PROFILE][::CONTAINER]
                                    The name of the browser to load cookies
                                    from. Currently supported browsers are:
                                    brave, chrome, chromium, edge, firefox,
                                    opera, safari, vivaldi, whale. Optionally,
                                    the KEYRING used for decrypting Chromium
                                    cookies on Linux, the name/path of the
                                    PROFILE to load cookies from, and the
                                    CONTAINER name (if Firefox) ("none" for no
                                    container) can be given with their
                                    respective separators. By default, all
                                    containers of the most recently accessed
                                    profile are used. Currently supported
                                    keyrings are: basictext, gnomekeyring,
                                    kwallet, kwallet5, kwallet6
    --cookies FILE                  Netscape formatted file to read cookies from
                                    and dump cookie jar in
    --ffmpeg-location PATH          Location of the ffmpeg binary; either the
                                    path to the binary or its containing
                                    directory
    --postprocessor-args NAME:ARGS  Give these arguments to the postprocessors.
                                    Specify the postprocessor/executable name
                                    and the arguments separated by a colon ":"
                                    to give the argument to the specified
                                    postprocessor/executable. Supported PP are:
                                    Merger, ModifyChapters, SplitChapters,
                                    ExtractAudio, VideoRemuxer, VideoConvertor,
                                    Metadata, EmbedSubtitle, EmbedThumbnail,
                                    SubtitlesConvertor, ThumbnailsConvertor,
                                    FixupStretched, FixupM4a, FixupM3u8,
                                    FixupTimestamp and FixupDuration. The
                                    supported executables are: AtomicParsley,
                                    FFmpeg and FFprobe. You can also specify
                                    "PP+EXE:ARGS" to give the arguments to the
                                    specified executable only when being used by
                                    the specified postprocessor. Additionally,
                                    for ffmpeg/ffprobe, "_i"/"_o" can be
                                    appended to the prefix optionally followed
                                    by a number to pass the argument before the
                                    specified input/output file, e.g. --ppa
                                    "Merger+ffmpeg_i1:-v quiet". You can use
                                    this option multiple times to give different
                                    arguments to different postprocessors.
                                    (Alias: --ppa)
    --extractor-args IE_KEY:ARGS    Pass ARGS arguments to the IE_KEY extractor.
                                    See "EXTRACTOR ARGUMENTS" for details. You
                                    can use this option multiple times to give
                                    arguments for different extractors
    -I, --playlist-items ITEM_SPEC  Comma-separated playlist_index of the items
                                    to download. You can specify a range using
                                    "[START]:[STOP][:STEP]". For backward
                                    compatibility, START-STOP is also supported.
                                    Use negative indices to count from the right
                                    and negative STEP to download in reverse
                                    order. E.g. "-I 1:3,7,-5::2" used on a
                                    playlist of size 15 will download the items
                                    at index 1,2,3,7,11,13,15
    --load-info-json FILE           JSON file containing the video information
                                    (created with the "--write-info-json"
                                    option)
    --skip-download                 Do not download the video but write all
                                    related files (Alias: --no-download)
    -s, --simulate                  Do not download the video and do not write
                                    anything to disk
    -i, --ignore-errors             Ignore download and postprocessing errors.
                                    The download will be considered successful
                                    even if the postprocessing fails
    --abort-on-error                Abort downloading of further videos if an
                                    error occurs (Alias: --no-ignore-errors)
    --no-warnings                   Ignore warnings
    -q, --quiet                     Activate quiet mode. If used with --verbose,
                                    print the log to stderr
    -v, --verbose                   Print various debugging information
    --dump-pages                    Print downloaded pages encoded using base64
                                    to debug problems (very verbose)
    --no-check-certificates         Suppress HTTPS certificate validation
    --parse-metadata [WHEN:]FROM:TO
                                    Parse additional metadata like title/artist
                                    from other fields; see "MODIFYING METADATA"
                                    for details. Supported values of "WHEN" are
                                    the same as that of --use-postprocessor
                                    (default: pre_process)
    --xattrs                        Write metadata to the video file's xattrs
                                    (using Dublin Core and XDG standards)
```
Otras verificadas de paso (útiles para H3): `-F, --list-formats`, `--check-formats`, `--audio-multistreams`, `--video-multistreams`,
`--no-mtime`, `--force-overwrites`, `--console-title`, `--progress-delta SECONDS` ("Time between progress output (default: 0)"),
`--write-info-json`, `--clean-info-json`/`--no-clean-info-json`.

Opciones que **NO existen**: ninguna de la lista pedida. (`--no-progress-template` tampoco existe, pero no se pidió.)

## 6. Progreso como JSON por línea (`--progress-template`)

Sintaxis del README ("OUTPUT TEMPLATE", "More Conversions"): además de los tipos `diouxXeEfFgGcrs`, yt-dlp admite
`B` (bytes), **`j` = json** (flag `#` para pretty-print, `+` para Unicode), `h` (HTML), `l` (lista separada por comas), `q` (quoted
para terminal), `D` (sufijos decimales), `S` (sanitizar como nombre de fichero). Travesía de objetos con `.`: `%(info.id)s`,
`%(progress.eta)s`; y `%(.{id,title})j` construye un dict con solo esas claves.

Claves del dict `progress` (docstring de `progress_hooks` en `yt_dlp/YoutubeDL.py` de esta versión + comprobación real):
- `status`: `"downloading"`, `"error"` o `"finished"` (comprobar primero; ignorar valores desconocidos)
- `filename` (siempre), `tmpfilename`, `downloaded_bytes`, `total_bytes` (None si desconocido), `total_bytes_estimate`,
  `elapsed`, `eta` (None si desconocido), `speed` (bytes/s, None si desconocido), `fragment_index`, `fragment_count`
  (solo en descargas fragmentadas: dash/hls), `ctx_id`, `info_dict` (NO se serializa con `%(progress)j`; usar `info.*`).
- Derivadas ya formateadas: `_percent` (float), `_percent_str`, `_eta_str`, `_speed_str`, `_total_bytes_str`,
  `_total_bytes_estimate_str`, `_downloaded_bytes_str`, `_elapsed_str`, `_default_template`.
- Postprocesado (`postprocess:`): `status` en `"started"`/`"processing"`/`"finished"`, `postprocessor` (p. ej. `Merger`,
  `MoveFiles`, `ExtractAudio`), `_default_template`.

**Comprobado**: `%(progress)j` funciona. Plantilla propuesta para mpvd (proceso hijo, stdout línea a línea):
```
yt-dlp --newline --no-warnings \
  --progress-template "download:MU_PROGRESS %(progress)j" \
  --progress-template "postprocess:MU_PP %(progress)j" \
  --print "after_move:MU_DONE %(.{id,title,ext,filepath,format_id,duration})j" \
  --no-simulate -P <dir> -o "%(id)s.f%(format_id)s.%(ext)s" -f "bv*+ba/b" <URL>
```
Líneas reales obtenidas (guardadas en `tests/fixtures/ytdlp/progress_lines_*.txt`):
```
MU_PROGRESS {"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 3871021, "tmpfilename": "tmp/ytdlp-test/aqz-KE-bpKQ.f139.m4a.part", "filename": "tmp/ytdlp-test/aqz-KE-bpKQ.f139.m4a", "eta": 10, "speed": 372180.87, "elapsed": 0.095, "ctx_id": null, "_eta_str": "00:10", "_speed_str": " 363.46KiB/s", "_percent": 0.026, "_percent_str": "  0.0%", "_total_bytes_str": "   3.69MiB", "_total_bytes_estimate_str": "       N/A", "_downloaded_bytes_str": "   1.00KiB", "_elapsed_str": "00:00:00", "_default_template": "  0.0% of    3.69MiB at  363.46KiB/s ETA 00:10"}
MU_PROGRESS {"downloaded_bytes": 3871021, "total_bytes": 3871021, "filename": "tmp/ytdlp-test/aqz-KE-bpKQ.f139.m4a", "status": "finished", "elapsed": 0.27, "ctx_id": null, "speed": 14316042.4, "_speed_str": "13.65MiB/s", "_total_bytes_str": "   3.69MiB", "_elapsed_str": "00:00:00", "_percent": 100.0, "_percent_str": "100.0%", "_default_template": "100% of    3.69MiB in 00:00:00 at 13.65MiB/s"}
MU_PP {"status": "started", "postprocessor": "Merger", "_default_template": "Merger started"}
MU_PP {"status": "finished", "postprocessor": "Merger", "_default_template": "Merger finished"}
MU_PP {"status": "started", "postprocessor": "MoveFiles", "_default_template": "MoveFiles started"}
MU_DONE "/home/.../tmp/ytdlp-test/Countdow1960.f0.mp3" Countdow1960 0        <- con --print "after_move:MU_DONE %(filepath)j %(id)s %(format_id)s"
```
Notas: al principio `eta`/`speed` pueden ser `null` (`_eta_str: "Unknown"`); las líneas `[youtube] ...`/`[download] Destination:` siguen
saliendo por stdout (filtrar por prefijo `MU_`), y las de aviso/error por stderr. Con `-f A+B` se emiten dos series de progreso
(una por fichero, `filename` distinto) y luego `Merger`. `--progress-delta 0.2` limita la frecuencia de líneas (útil para no saturar el OSD).

### URL de red que funciona hoy para tests `@network` (< 20 MB)
- **Recomendada**: `https://archive.org/details/Countdow1960` — "[Countdown Leader: Technicolor Corporation]", dominio público
  (Prelinger), 14.1 s. Formatos: `0` mp3 (114 688 B), `1` mp4 321x240 (1 060 032 B), `2` ogv (774 107 B), `3` mp4 643x480 (1 471 490 B).
  Descarga real OK con progreso JSON. Extractor `archive.org` (sin JS, sin cookies). Aviso inofensivo:
  `WARNING: "asr" field is not numeric - forcing int conversion, there is an error in extractor`.
- Alternativa: `https://archive.org/details/DogCart1947` (23.6 s, mp4 480p 2 506 681 B, dominio público).
- YouTube (si se quiere probar YouTube de verdad): `https://www.youtube.com/watch?v=aqz-KE-bpKQ` con `-f 139` (audio AAC 3.69 MiB)
  o `-f 160+139` (144p+audio, 8.4 MB tras merge). Funciona hoy sin runtime JS. `BigBuckBunny_124` de archive.org es demasiado grande
  (mínimo 46 MB) para descarga en test; sirve solo para `-J`.

## 7. Fixtures guardados (`tests/fixtures/ytdlp/`)

| Fichero | Tamaño | Origen |
|---|---|---|
| `youtube_bbb.json` | 152 769 B | `-J https://www.youtube.com/watch?v=aqz-KE-bpKQ` **sin** runtime JS (cliente visionos) |
| `youtube_bbb_node.json` | 151 933 B | Ídem con `--js-runtimes node` |
| `archive_bbb.json` | 14 231 B | `-J https://archive.org/details/BigBuckBunny_124` (3 formatos: ogv 46 MB, mp4 61 MB, avi 332 MB) |
| `archive_countdown.json` | 15 034 B | `-J https://archive.org/details/Countdow1960` (URL de test) |
| `playlist_flat.json` | 7 201 B | `--flat-playlist -I 1:5 -J https://www.youtube.com/@BlenderOfficial/videos` (5 entradas) |
| `playlist_flat_search.json` | 5 982 B | `--flat-playlist -J "ytsearch5:big buck bunny blender"` |
| `progress_lines_youtube_160+139.txt` | 16 916 B | líneas `MU_PROGRESS`/`MU_PP` de una descarga real 160+139 |
| `progress_lines_archive_countdown.txt` | 7 272 B | ídem para Countdow1960 formato 3 |

Se ha anonimizado la IP pública de esta máquina (`ip=...` en las URLs de googlevideo, sustituida por `0.0.0.0`); las URLs de
googlevideo caducan (`expire=`) a las ~6 h, así que los fixtures sirven para parsear, no para descargar.

## 8. Estructura de `-J` (comprobada sobre los fixtures)

### Nivel vídeo (`_type` ausente o `"video"`)
Claves presentes en YouTube: `id`, `title`, `fulltitle`, `description`, `duration` (s, int), `duration_string`, `thumbnail`,
`thumbnails` (lista de `{url,width,height,preference,id,resolution}`), `chapters` (lista de `{start_time,end_time,title}` o `null`),
`subtitles` y `automatic_captions` (dict `lang -> [{ext,url,name,protocol?}]`; vacíos en BBB), `webpage_url`, `original_url`,
`extractor` (`youtube`), `extractor_key` (`Youtube`), `formats`, `requested_formats` (los formatos elegidos, misma estructura que
`formats`), `requested_downloads` (`format_id`, `ext`, `protocol` `https+https`, `filename`, `_filename`), `format_id` (`401+251`),
`format`, `ext`, `resolution`, `width/height/fps/vcodec/acodec/abr/vbr/tbr/asr/audio_channels/dynamic_range/aspect_ratio` (del formato
elegido), `uploader`, `uploader_id`, `uploader_url`, `channel`, `channel_id`, `channel_url`, `channel_follower_count`,
`channel_is_verified`, `upload_date` (`YYYYMMDD`), `timestamp`, `release_timestamp`, `release_year`, `like_count`, `view_count`,
`comment_count`, `average_rating`, `live_status` (`not_live`, `is_live`, `is_upcoming`, `was_live`, `post_live`), `is_live`, `was_live`,
`availability` (`public`), `age_limit`, `license`, `categories`, `tags`, `media_type`, `heatmap` (lista `{start_time,end_time,value}`),
`playable_in_embed`, `epoch`, `_version` (`{version, release_git_head, repository}`), `_has_drm`, `_format_sort_fields`.
archive.org: `title`, `duration` (float), `license` (URL), `formats`, `thumbnails` (con `filesize`), `uploader`, `upload_date`, etc.

### Por formato (`formats[]`)
Unión de claves en YouTube: `format_id`, `format`, `format_note` (`2160p60`, `medium`, `low, DRC`, `storyboard`, `Default, low`),
`format_index`, `ext`, `video_ext`, `audio_ext`, `vcodec` (`av01.0.13M.08`, `vp09...`, `avc1.4d400c`, **`"none"`** si solo audio,
**`"images"`** en storyboards según `-F`, y en el JSON `"none"` + `protocol: "mhtml"`), `acodec` (`opus`, `mp4a.40.5`, **`"none"`** si solo
vídeo), `width`, `height`, `fps`, `resolution` (`3840x2160` / `audio only`), `aspect_ratio`, `tbr`, `vbr`, `abr`, `asr`, `audio_channels`,
`filesize`, `filesize_approx`, `dynamic_range` (`SDR`/`HDR`), `container` (`mp4_dash`, `webm_dash`), `protocol` (`https`, `m3u8_native`,
`mhtml`), `manifest_url` (solo m3u8), `fragments` (solo storyboards/m3u8), `rows`/`columns` (storyboards), `language`,
`language_preference`, `preference`, `source_preference`, `quality` (float), `has_drm` (bool), `available_at`, `url`, `http_headers`
(dict `User-Agent`, `Accept`, `Accept-Language`, `Sec-Fetch-Mode`), `downloader_options` (`{http_chunk_size: 10485760}`).
- **YouTube no ofrece ya formatos combinados** (vídeo+audio): 16 formatos con `vcodec: "none"`, 41 con `acodec: "none"`, 0 con ambos.
  Para el menú "Calidad" hay que agrupar: solo vídeo (`acodec=="none"`), solo audio (`vcodec=="none"`), storyboards (`protocol=="mhtml"`),
  HLS (`protocol=="m3u8_native"`, algunos con `vcodec: "none"` y `acodec: null` = audio HLS sin codec conocido).
- archive.org: `vcodec`/`acodec` vienen a **`null`** (no `"none"`), `width/height/filesize/url/protocol: https` sí; `ext` `ogv/mp4/avi/mp3`.
  El código no debe asumir que `vcodec` sea string.

### Playlist plana (`--flat-playlist -J`)
Raíz: `_type: "playlist"`, `id`, `title`, `entries[]`, `playlist_count` (puede ser `null` con `-I`), `requested_entries`,
`extractor`/`extractor_key` (`youtube:tab`/`YoutubeTab`), `channel*`, `uploader*`, `webpage_url`, `thumbnails`, `_version`, `__files_to_move`.
Cada entrada: `_type: "url"`, `ie_key: "Youtube"`, `id`, `url` (`https://www.youtube.com/watch?v=...`), `title`, `duration`,
`thumbnails`, `view_count`, `live_status`, `availability`, `timestamp`, `channel_url`, `uploader_url`, `creators`, `__x_forwarded_for_ip` (null).

## 9. `--audio-quality`, `--audio-format`, `--remux-video` (línea de ayuda exacta en la sección 5)
- `--audio-quality QUALITY`: **VBR** = entero `0` (mejor) … `10` (peor), por defecto `5`; **bitrate constante** = número con sufijo `K`,
  p. ej. `128K` (también admite `256K`, etc.). Solo tiene efecto con `-x` cuando ffmpeg reencodea.
- `--audio-format FORMAT`: `best` (por defecto = no reencodear, solo extraer), `aac`, `alac`, `flac`, `m4a`, `mp3`, `opus`, `vorbis`, `wav`.
  Admite reglas `origen>destino/...` como `--remux-video`.
- `--remux-video FORMAT`: `avi, flv, gif, mkv, mov, mp4, webm, aac, aiff, alac, flac, m4a, mka, mp3, ogg, opus, vorbis, wav`; reglas
  `"aac>m4a/mov>mp4/mkv"`. `--recode-video` usa la misma sintaxis y lista. `--merge-output-format`: `avi, flv, mkv, mov, mp4, webm`.

## 10. Decisiones sugeridas para H3
1. Vendorizar el asset zipimport `yt-dlp` (3 MB) en `vendor/bin/yt-dlp` y ejecutarlo siempre con `.venv/bin/python` (3.12) desde mpvd;
   para `ytdl_hook` de mpv (`--script-opts=ytdl_hook-ytdl_path=`) apuntar al mismo fichero ejecutable (shebang `python3`).
2. Pasar siempre `--no-update --no-remote-components` (nada de red ni escritura fuera de lo previsto) y `--js-runtimes node` si `node` >= 22
   existe; vendorizar `deno` v2.9.7 (41 MB zip) como opción en `tools/vendor.sh` con `--js-runtimes deno:vendor/bin/deno`.
3. Selectores de formato siempre con `*`/`+` (`bv*+ba/b`, `ba/b`), nunca `best`/`worst` a secas.
4. Test `@network`: `https://archive.org/details/Countdow1960` formato `3` (1.4 MB) y, opcionalmente, YouTube `aqz-KE-bpKQ -f 139`.

## 11. Búsqueda, orden por contenedor y runtime JS desde la primera carga (2026-09-30)
- **Runtime JS en la primera carga**: con `mpv-uos <url>` el primer `yt-dlp -J` de ytdl_hook salía sin `--js-runtimes` (mu-ytdl solo lo
  añadía al responder mpvd, ~0,5 s después) y yt-dlp 2026.08.19 solo habilita deno por defecto. Ahora mu-ytdl añade
  `ytdl-raw-options js-runtimes=node` de forma síncrona al cargar (opción `mu-ytdl-js_runtimes`; mpv espera al bloque principal de los
  scripts antes del primer archivo) y mpvd lo sustituye después por `node:<ruta>` o por el deno vendorizado. Un valor puesto por el
  usuario en `mpv.conf`/línea de órdenes no se toca. Sin node en PATH yt-dlp solo lo marca *unavailable*.
  Test: `tests/test_mu_ytdl_palettes.py::test_first_ytdl_run_already_has_js_runtime`.
- **`-S` por contenedor** (presets de vídeo, no formatos exactos): sin él, «Vídeo · 360p» en mp4 bajaba AV1 + Opus y el merge caía a
  `.mkv`. `mp4` → `-S vcodec:h264,res,acodec:aac` (el mejor códec *no mejor que* H.264; `res` justo después para que un 360p combinado
  no gane a un 1080p solo vídeo por el audio), `webm` → `-S vcodec:vp9,res,acodec:opus`, `mkv` → sin `-S`. Comprobado con el yt-dlp
  vendorizado sobre `youtube_bbb.json` (`--load-info-json --simulate`): 360p mp4 → `134+140` (.mp4), mejor mp4 → `299+140` (1080p .mp4),
  360p webm → `243+251` (.webm), 360p mkv → `396+251` (AV1). Semántica de `campo:límite` en `FormatSorter` (yt_dlp/utils/_utils.py).
- **Búsqueda** `ytdl.search {query, limit=15}` → `yt-dlp --flat-playlist -J -- ytsearchN:<consulta>` con el mismo binario y argumentos
  seguros (`--no-update --no-remote-components`, `--js-runtimes`), 30 s de tope, caché en memoria de 1 h por (consulta normalizada,
  límite) y una sola ejecución para búsquedas idénticas simultáneas. Devuelve `[{url, title, duration, channel, view_count, is_live}]`
  (`is_live` = `live_status == "is_live"` de la insignia de la búsqueda; `channel` cae a `uploader`). ~3 s por búsqueda real.

## 12. Guardados de Instagram y TikTok: qué soporta de verdad el yt-dlp instalado (2026-10-01, H37/D4)
Comprobado **contra el binario vendorizado** (2026.08.19) preguntando a cada extractor si acepta la URL
(`gen_extractor_classes()` + `suitable()`, sin red ni cuentas, porque lo único que se quería saber es si existe el
extractor):

| URL | Extractor que la acepta | Estado |
|---|---|---|
| `https://www.instagram.com/<perfil>/saved/all-posts/` | **ninguno** | no se puede: no hay extractor de «guardados» |
| `https://www.instagram.com/<perfil>/` | `instagram:user` | existe, pero `--list-extractors` lo marca **CURRENTLY BROKEN** |
| `https://www.tiktok.com/@<perfil>` | `tiktok:user` | funciona |
| `https://www.tiktok.com/@<perfil>/collection/<nombre>-<id>` | `tiktok:collection` | funciona |
| `https://www.tiktok.com/favorite` | **ninguno** | no se puede: no hay extractor de favoritos |

Consecuencia para el menú de descargas: la caja de enlaces reconoce estas formas y **dice lo que va a pasar antes de
intentarlo** (que es lo que pidió Ser que no fallara en silencio): los guardados de Instagram y los favoritos de TikTok
se rechazan con una explicación y la alternativa (pegar los enlaces de los vídeos, que sí funcionan uno a uno), el perfil
de Instagram se acepta avisando de que yt-dlp lo marca roto en esta versión, y el perfil o la colección de TikTok se
aceptan recordando que para lo privado hace falta *Usar mi sesión del navegador* (cookies, apagado por defecto).
Al cambiar de versión de yt-dlp conviene repetir la comprobación: el test `test_ytdl_sitios.py` la hace sola.
