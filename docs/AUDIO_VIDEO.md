# Sonido e imagen (mu-av): filtros validados contra el mpv instalado

mpv 0.41 enlaza la libavfilter de FFmpeg 8.0.1 del sistema; `mpv --af=help` / `mpv --vf=help` listan los filtros lavfi disponibles
y cada grafo de abajo se ejecutó en headless (`--vo=null --ao=null --end=3`) sin errores en el log (2026-09-29, pc-latitude5480).
Los filtros se añaden con etiqueta para poder quitarlos sin tocar el resto de la cadena: `af add @mu-<x>:lavfi=[grafo]` /
`af remove @mu-<x>` (ídem `vf`). Estado publicado en `user-data/mu/av` (`filters`, `light`, `models`, `diag`).

| Entrada del menú | Cadena | Grafo lavfi | Notas |
|---|---|---|---|
| Diálogo claro | af | `highpass=f=70,dynaudnorm=f=250:g=11:p=0.85:m=8,equalizer=f=2800:t=q:w=1.2:g=2.5` | quita el retumbe, nivela y realza la banda de la voz |
| Modo noche (`alt+n`) | af | `acompressor=threshold=-24dB:ratio=6:attack=5:release=400:makeup=4dB,alimiter=limit=0.7` | techo configurable `mu-av-night_limit` |
| Reducción de ruido | af | `arnndn=m=<modelo.rnnn>:mix=0.9` · sin modelo: `afftdn=nr=12:nf=-40` | modelos RNNoise de GregorR/rnnoise-models (BSD), ~300 KB, descarga bajo demanda (`av.models.download rnnoise-sh`) |
| Binaural para auriculares | af | `sofalizer=sofa=<hrtf.sofa>:type=freq` · sin SOFA: `crossfeed=strength=0.5:range=0.5` | HRTF MIT KEMAR (1,1 MB, sofacoustics.org); libmysofa está compilado en el FFmpeg del sistema |
| Protección fotosensible | vf | `photosensitivity=frames=30:threshold=1:bypass=0` | atenúa destellos rápidos; coste bajo en CPU |
| Perfil ligero | opciones | `scale/dscale/cscale=bilinear`, `deband=no`, `interpolation=no`, `video-sync=audio` | se guardan y restauran los valores previos |

## Diagnóstico de tirones
Lee `frame-drop-count`, `decoder-frame-drop-count`, `mistimed-frame-count`, `vo-delayed-frame-count`, `estimated-vf-fps`,
`container-fps`, `display-fps`, `estimated-display-fps`, `hwdec-current`, `current-vo`, `video-sync`, `interpolation` y propone:
perfil ligero (pérdidas en el VO), `hwdec=auto-safe` (pérdidas en el decodificador con hwdec=no), `video-sync=display-resample`
(fotogramas fuera de tiempo) y `interpolation=yes` (cadencia irregular: fps del contenedor no múltiplo de la pantalla).

## Modelos (`av.*` en mpvd)
`av.models` (presentes/descargables con ruta), `av.models.download {name}` (eventos `av-model` con progreso), `av.models.path {name}`.
Fijados por SHA-256 en vendor.lock: `rnnoise-sh` (general), `rnnoise-bd` (voz con ruido), `sofa-kemar`. Directorios:
`MPV_UOS_AV_MODELS`, `vendor/models/<kind>/`, `<data_dir>/models/<kind>/`.

## Alternativas disponibles (no expuestas todavía)
`anlmdn`, `speechnorm`, `loudnorm` (dos pasadas), `deesser`, `stereotools`, `haas`, `extrastereo`; vídeo: `deband`, `deflicker`,
`hqdn3d`, `nlmeans` (caro), `tmix`, `unsharp`, `eq`.

## Música (mu-music, H32, ADR-064)
Opciones verificadas en el manual de mpv 0.41 instalado (`man mpv`):
- **Sin cortes**: `gapless-audio=yes` + `prefetch-playlist=yes`. Apagado vuelve al valor por defecto de mpv (`weak`: sin
  cortes solo si el formato de audio coincide).
- **Volumen igualado**: `replaygain=track|album` para los archivos con etiquetas `REPLAYGAIN_*` (mpv las lee y evita el
  recorte con el pico). Para los que no las tienen, mpvd mide la pista con `ffmpeg -af ebur128=peak=sample` (ReplayGain 2.0:
  −18 LUFS − sonoridad integrada; el filtro `replaygain` de ffmpeg implementa ReplayGain 1 y da +2,5 dB más) y mu-music
  aplica el valor con la opción local del archivo `replaygain-fallback`, que mpv solo usa cuando no hay etiquetas. mpv no
  aplica `replaygain-preamp` ni la protección contra recortes a ese valor: lo hace mu-music (`min(ganancia, −20·log10(pico))`).
  La ganancia de álbum es la media energética de sus pistas ponderada por duración. Nunca se escriben los archivos.
  Coste medido: 0,9 s para 5 min de MP3 con pico de muestra (2,6 s con pico real, descartado).
- **Fundido**: mpv reproduce un único flujo de audio y `acrossfade` necesita dos entradas simultáneas, así que un fundido
  cruzado real no es posible. mu-music baja `volume-gain` al final de la pista (N s) y lo sube al empezar la siguiente
  (N/2 s), a 20 Hz solo durante el fundido; `volume-gain` se suma al volumen del usuario y no se guarda en watch_later.
- **Salida exclusiva**: `audio-exclusive=yes` (solo PipeWire, WASAPI, CoreAudio y AudioUnit).
- **Ecualizador**: los perfiles de mu-av, más tres para tipos de auriculares (de botón, cerrados y abiertos) con
  `lowshelf`/`highshelf`/`equalizer` y el limitador sin auto-nivel de los demás perfiles.
