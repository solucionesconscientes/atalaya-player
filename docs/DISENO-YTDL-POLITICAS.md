# Políticas de descarga en Atalaya: alinear el yt-dlp con los bots

Estado: propuesta, 31-oct-2026. No hay código tocado todavía.
Referencia de la política: `~/.local/share/ytdl/ytdlbot/formats.py` (`plan()`, `elegir_audio()`,
`decision_audio()`, `_orden_video()`), probada y en producción desde el 30-oct-2026.

## 1. Qué hace hoy Atalaya (medido en el código)

| Pieza | Dónde | Qué hace |
|---|---|---|
| Presets del menú | `mpvd/ytdl/presets.py:290-330` | `video_1080` es el de por defecto (`main.lua:48`); audio: original, MP3 128/192/320/VBR, Opus 128, M4A 192, FLAC, WAV |
| Expresión de formato | `presets.py:120-137` | `bv*[height<=?N]+ba/b[height<=?N]/bv*+ba/b` |
| Orden de códecs | `presets.py:47-49` | `-S vcodec:<el que decodifica la máquina>,res,acodec:opus` |
| Contenedor | `presets.py:40-42`, `main.lua:616` | por defecto **mp4**, con `--merge-output-format mp4` y `--remux-video mov>mp4/...` |
| Menú «Calidad» | `main.lua:547-585` | agrupa formatos del `-J` en vídeo+audio / solo vídeo / solo audio; al elegir vídeo hace `id .. '+ba'` |
| Listas | `service.py:457`, `main.lua:667` | «Lista completa» es un interruptor, sin tope ni estimación |
| Cola | `downloads.py:120` | 2 descargas a la vez, 5 reintentos, `--ignore-errors` |

## 2. Fallos de diseño (y por qué se notan)

1. **`mp4` forzado con cualquier códec.** El propio código documenta que ffmpeg mete Opus dentro de mp4. Es
   cierto y es un error de producto: ese `.mp4` no lo abren televisiones, consolas, coches ni el decodificador de
   hardware de muchos móviles, y en algunos reproductores no se puede ni buscar dentro. El perfil por defecto
   (`video_1080` + `mp4`) acaba justo en la peor combinación: **VP9 u H.265 con Opus dentro de MP4**.
2. **Elegir vídeo siempre empareja con «el mejor audio».** `quality_item()` construye `row.id .. '+ba'`: no se
   puede pedir una pista de vídeo concreta con una pista de audio concreta. En un vídeo con doblajes, el usuario
   no puede decir «1080p con el audio español 140».
3. **El orden de códecs prioriza hardware, no tamaño.** `-S vcodec:h264` (o h265) elige H.264 1080p a 4 Mbps
   antes que VP9 1080p a 1,6 Mbps. Para descargar y guardar es al revés de lo que interesa; para reproducir en
   ese equipo da igual, porque mpv decodifica VP9 sobradamente.
4. **El audio por defecto no es el del origen.** El menú ofrece MP3 128/192/320, M4A, FLAC y WAV como iguales;
   cuatro de cada cinco opciones recodifican. La política de los bots —Opus del origen, sin tocar— no aparece
   como «recomendado» en ninguna parte.
5. **No distingue voz de música.** En voz, el Opus más bajo (≈50 k) es indistinguible y ahorra la mitad; en
   música, el más alto (≈128 k) es el único que conserva detalle. Hoy se pide siempre lo mismo.
6. **No hay «qué se va a bajar» antes de bajar.** Se elige un preset y arranca: ni formato exacto, ni
   contenedor, ni tamaño estimado. El bot enseña la ficha (`VP9 720p · OPUS 128 kbps · MKV · 210 MB`) antes
   de encolar; aquí se descubre al terminar.
7. **Listas sin freno.** «Lista completa» no tiene tope de elementos, ni estimación de tamaño, ni comprobación
   de espacio libre. Un canal de 400 vídeos a 1080p son cientos de GB y horas de descarga, y el usuario lo
   descubre cuando el disco se llena.
8. **Sin archivo de descargas.** Al relanzar una lista, `--no-overwrites` protege el nombre, pero un título
   cambiado genera un duplicado. El bot usa `--download-archive` siempre en listas.
9. **Los fallos se pierden.** `--ignore-errors` hace que una lista siga, bien; pero no hay informe por ítem
   («3 de 40 fallaron: <ids>»), así que una lista «terminada» puede tener agujeros.
10. **Combinaciones imposibles se intentan.** El propio código avisa de que remuxear a un contenedor que no
    admite el códec falla; se descubre al final, con el fichero a medio escribir, en vez de rechazarse al
    elegir con el motivo.

## 3. Política propuesta (una sola tabla de decisión)

### 3.1 Perfiles

| Perfil | Vídeo | Audio | Contenedor | Recodifica |
|---|---|---|---|---|
| `opus_origen` (audio) | — | **voz** → Opus más bajo; **música** → Opus más alto | `.opus` | no |
| `video_720` (defecto) | VP9 ≤720p, **el de menor bitrate a esa altura** | Opus según voz/música | **MKV** | no |
| `compatible` | H.264 ≤1080p | AAC 140 | **MP4** | no |
| `pistas` | la elegida | la elegida | por pareja | no |
| `convertir` | — | MP3/FLAC/WAV/Opus 128 | según | **sí, explícito** |

Clasificación música/voz: `categories` con `music`, o `track`/`artist` presentes → música; si no, voz. Es la
misma regla que `formats.es_musica()` del bot, ya probada.

### 3.2 Contenedor por pareja (rechazo con motivo, nunca silencio)

| Vídeo | Audio | Contenedor |
|---|---|---|
| H.264 / HEVC | AAC | **mp4** |
| H.264 / HEVC | Opus | mkv |
| VP9 / AV1 | AAC | mkv |
| VP9 / AV1 | Opus | mkv (webm si va suelto) |
| cualquiera | Opus en mp4 | **rechazar**: «Opus no es estándar en MP4; usa MKV» |
| cualquiera | AAC en webm | **rechazar** |

### 3.3 Reglas duras (las mismas que el bot)

- El defecto **no recodifica**. Convertir es una acción aparte, con su nombre.
- A igual altura, **gana el bitrate más bajo**: `-S res,tbr` ascendente en la altura elegida (equivalente a
  `_orden_video()` del bot).
- Con pistas elegidas a mano, `-f "<video_id>+<audio_id>"` y `-c copy`; el plan lleva `recodificar=False`.
- El hardware deja de mandar: solo desempata dentro de `compatible`, cuyo objetivo es justo ese.

## 4. Cómo se incorpora (por ficheros)

### 4.1 Nuevo `mpvd/ytdl/policy.py` (~250 líneas)

```python
Perfil = Literal["opus_origen", "video_720", "compatible", "pistas", "convertir"]

@dataclass(frozen=True)
class Decision:
    video_id: str | None
    audio_id: str | None
    contenedor: str            # "mkv" | "mp4" | "opus"
    recodificar: bool          # siempre False salvo perfil "convertir"
    nota: str                  # «Opus del origen, el más bajo (voz)»
    bytes_estimados: int | None

def clasificar(info: dict) -> Literal["musica", "voz"]
def pistas(info: dict) -> dict          # {"video": [...], "audio": [...]} con kbps y bytes
def plan(info: dict, perfil: str, video_id=None, audio_id=None) -> Decision
def contenedor_valido(vcodec: str, acodec: str, pedido: str | None) -> tuple[str | None, str | None]
```

`pistas()` no necesita consultar nada más: `info.py` ya expone `tbr`, `abr`, `channels` y `filesize`.

### 4.2 `presets.py`

- Presets nuevos, en este orden y como únicos visibles:
  1. `audio_opus_origen` — «Audio · Opus del origen (recomendado)»
  2. `video_720` — «Vídeo · 720p VP9 + Opus (recomendado)»
  3. `compatible` — «Vídeo · compatible (H.264 + AAC, MP4)»
  4. `pistas` — «Elegir pistas del origen…»
- Los de conversión (MP3, FLAC, WAV, Opus 128, M4A) pasan a un grupo `convertir`, detrás de una entrada
  secundaria «Convertir audio…».
- `DownloadSpec` gana `video_id` y `audio_id`; `format` libre se queda solo para `exact` (avanzado).
- `build_args()`: si hay ids → `-f "<v>+<a>"`, `-c copy`, contenedor del plan; valida con
  `policy.contenedor_valido()` **antes** de lanzar.

### 4.3 `service.py`

- `ytdl.plan` (RPC nuevo): `{url, perfil, video_id?, audio_id?}` → `Decision` serializada. Es lo que pinta la
  pantalla de confirmación.
- `ytdl.pistas` (RPC nuevo): listas reales para el selector.
- `ytdl.download` acepta `video_id`/`audio_id` y llama a `policy.plan()`; si la pareja no vale, error con
  motivo (no excepción de ffmpeg a mitad).

### 4.4 `downloads.py`

- `max_items` por descarga de lista (50 por defecto, ajustable): si la lista trae más, se pide confirmación y
  se ofrece «los primeros N».
- Comprobación de espacio: `bytes_estimados × 1,2 + 2 GB` contra `shutil.disk_usage`; si no cabe, se dice
  antes de empezar.
- `--download-archive` **activo por defecto** en listas (`<salida>/.atalaya-archive.txt`).
- Informe final por ítem: `ok/total`, y la lista de fallidos con motivo (parseo de stderr ya existente).
- No encolar dos veces el mismo `(id, formato, contenedor)`.

### 4.5 `mpv-config/scripts/mu-ytdl/main.lua`

- Menú «Descargar»: los cuatro perfiles, con el plan debajo («VP9 720p 1,2 Mbps + Opus 128k → MKV · ~210 MB»).
- Vista nueva «Pistas del origen»: dos listas (vídeo / audio) con ✅ y ♻️ Todo a Auto, igual que el bot; al
  combinar, enseña el contenedor resultante y el tamaño; las parejas imposibles salen deshabilitadas con el
  motivo.
- En listas: «N vídeos · ~X GB · carpeta <título>/» y el tope, antes de empezar.
- «Calidad» se queda como está para lo que ya hace bien: lista de formatos para inspeccionar y para `exact`.

## 5. Fases y criterio de hecho

| Fase | Trabajo | Hecho cuando |
|---|---|---|
| F1 | `policy.py` + tests + presets nuevos + `build_args` con ids | tests de la tabla de contenedores y de voz/música en verde; `-f` correcto en 6 casos |
| F2 | RPC `plan`/`pistas` + vista «Pistas del origen» + confirmación | una descarga real de música y otra de voz acaban en `.opus` sin recodificar (ffprobe) |
| F3 | topes de lista, archivo, espacio, informe de fallos | lista de prueba de 10 ítems: 2 fallan a propósito y el informe los nombra |
| F4 | `compatible` y comprobación en TV/Windows | MP4 H.264+AAC abierto en un televisor y en Windows sin avisos |
| F5 | docs: `docs/YTDLP.md` + ADR | la tabla de decisión queda escrita donde ya vive la de presets |

Esfuerzo aproximado: F1 4-6 h, F2 3-4 h, F3 2-3 h, F4 depende del hardware, F5 1 h.

## 6. Qué NO se porta, y por qué

- **Cuotas por usuario y límites de 50 MB / 2 GB**: son del reparto por Telegram, no de una app de escritorio.
- **Mensajes, botones y `caption`**: la UI es uosc, no Telegram.
- **IA, transcripción y biblioteca**: ya existen como módulos propios (`asr/`, `subs/`) y no tienen que ver con
  la descarga.
- **`.info.txt`**: aquí ya se escriben `.srt` y metadatos incrustados.

## 7. Divergencia deliberada con el bot

El bot optimiza **tamaño y compatibilidad para compartir**; Atalaya optimiza **reproducir en este equipo**. Por
eso:

- `video_720` (VP9 + Opus, MKV) es el defecto aquí, mientras el bot usa 720p VP9 por tamaño.
- Se conserva la preferencia de hardware, pero **solo como desempate** dentro de `compatible`.
- En audio, la política es idéntica: Opus del origen, música el mejor y voz el más bajo, sin recodificar.
