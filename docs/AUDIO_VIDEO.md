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
