# Mando remoto: API de dibujo (QR en pantalla), codificador QR y red

Verificado el 2026-09-29 contra las fuentes instaladas en `dell` (mpv v0.41.0, libplacebo 7.360, FFmpeg 8.0.1, Python 3.12.14 en `.venv`,
uosc 5.13.0 vendorizado). Scripts de prueba reutilizables en `tmp/` (ignorado por git): `ov_test.py`, `ov_lua_test.lua`, `ov_ipc_big.py`,
`qr_check.py`, `qr_check2.py`, `net_test.py`.

## 1. `overlay-add` (bitmap) en mpv 0.41

Sintaxis (man mpv, sección "List of Input Commands"):

> `overlay-add <id> <x> <y> <file> <offset> <fmt> <w> <h> <stride> <dw> <dh>`
> Add an OSD overlay sourced from raw data. [...] The order of them is not guaranteed, so you should always call them with named arguments.
> `id` is an integer between 0 and 63 [...] Using a previously unused ID will add a new overlay, while reusing an ID will update it.
> `file` specifies the file the raw image data is read from. It can be either a numeric UNIX file descriptor prefixed with @ (e.g. @4), or a
> filename. The file will be mapped into memory with mmap(), copied, and unmapped before the command returns (changed in mpv 0.18.1).
> It is also possible to pass a raw memory address for use as bitmap memory by passing a memory address as integer prefixed with an &
> character. Passing the wrong thing here will crash the player. This mode might be useful for use with libmpv.
> `fmt` is a string identifying the image format. Currently, only `bgra` is defined. [...] This uses premultiplied alpha.
> `w`, `h`, and `stride` [...] In the simple case, and with the bgra format, stride==4*w.
> `dw` and `dh` specify the (optional) display size of the overlay.
> `overlay-remove <id>`: Remove an overlay added with overlay-add and the same ID. Does nothing if no overlay with this ID exists.

- Sí acepta `@fd` y `&dirección` (esta última solo tiene sentido con libmpv; por IPC es peligrosa). Solo `bgra` premultiplicado.
- **Prueba real headless** (`mpv --no-config --vo=null --ao=null --idle=yes --input-ipc-server=tmp/ov.sock`, fichero BGRA 64x64 de 16384 bytes):

| Comando enviado por el socket | Respuesta exacta |
|---|---|
| `["overlay-add",1,10,10,"<ruta>",0,"bgra",64,64,256]` | `{"data":null,"request_id":2,"error":"success"}` |
| `{"name":"overlay-add","id":2,"x":20,"y":20,"file":"<ruta>","offset":0,"fmt":"bgra","w":64,"h":64,"stride":256}` | `{"data":null,"error":"success"}` |
| fmt `rgba` | `{"error":"error running command"}` (log: `overlay-add: unsupported OSD format 'rgba'`) |
| fichero inexistente | `{"error":"error running command"}` (log: `could not open or map '...'`) |
| id 64 | `{"error":"error running command"}` (log: `overlay-add: invalid id 64`) |
| `["overlay-remove",1]` / id inexistente 99 | `{"data":null,"error":"success"}` en ambos casos |

  Con `--vo=null` no hay error ni restricción: el comando se acepta y simplemente no se ve. `osd-dimensions` devuelve todo 0 en headless.
- Inconveniente para el QR: exige escribir un fichero (o fd) BGRA y las coordenadas son en píxeles de pantalla (hay que leer `osd-width/height`
  y reescalar al cambiar la ventana). Los bitmaps de `overlay-add` quedan siempre por encima de los overlays ASS (man: "bitmap overlays
  added by overlay-add are always on top of the ASS overlays added by osd-overlay").

## 2. `osd-overlay` / `mp.create_osd_overlay` (vectores ASS) — vía recomendada

man mpv, comando `osd-overlay`:

> You can use this to add text overlays in ASS format. ASS has advanced positioning and rendering tags, which can be used to render almost any
> kind of vector graphics. [...] `id`: Arbitrary integer [...] There is a separate namespace for each libmpv client (i.e. IPC connection,
> script) [...] connecting via --input-ipc-server, adding an overlay, and disconnecting will remove the overlay immediately again.
> `format`: `ass-events` (The data parameter is a string. The string is split on the newline character. Every line is turned into the Text part
> of a Dialogue ASS event) | `none` (Special value that causes the overlay to be removed).
> `data`: String defining the overlay contents. `res_x, res_y`: [...] Optional, defaults to 0/720. `z`: The Z order of the overlay. Optional,
> defaults to 0. `hidden`: If set to true, do not display this (default: false). `compute_bounds`: [...] write them to the command's result
> value as x0, x1, y0, y1 [...] This feature is experimental.
> Note: Always use named arguments (mpv_command_node()). Lua scripts should use the mp.create_osd_overlay() helper instead of invoking this
> command directly.

man mpv, sección Lua:

> `mp.create_osd_overlay(format)`: Create an OSD overlay. This is a very thin wrapper around the osd-overlay command. The function returns a
> table, which mostly contains fields that will be passed to osd-overlay. [...] `update()`: Commit the OSD overlay to the screen [...] Returns the
> result of the osd-overlay command itself. `remove()`: Remove the overlay from the screen. A update() call will add it again.
> The advantage of using this wrapper [...] is that the id field is allocated automatically.

Comprobado en Lua headless (`tmp/ov_lua_test.lua`): el objeto recién creado trae `data="", format="ass-events", id=1, res_x=0, res_y=720`;
`z`, `hidden` y `compute_bounds` se añaden como campos y se pasan al comando (con `compute_bounds=true` el `update()` devolvió
`{"x0":0,"y0":0,"x1":271,"y1":271}` para un cuadrado de 264 px en PlayRes 720). `mp.assdraw` (módulo embebido, no documentado en la man)
ofrece: `new_event, append, pos, an, draw_start, draw_stop, move_to, line_to, rect_cw, rect_ccw, round_rect_cw/ccw, bezier_curve, hexagon_*, merge`.
uosc dibuja exactamente así: `main.lua:12 osd = mp.create_osd_overlay('ass-events')` y `lib/ass.lua:197 ass_mt:rect()` emite
`{\pos(0,0)\rDefault\an7\blur0\bord0\1c&H...}` + `draw_start()` + `rect_cw()` + `draw_stop()`.

Rendimiento medido (headless, PlayRes 720, QR v4 33x33 a 8 px/módulo):
- 1089 rectángulos sin fusionar → 47 701 caracteres; `update()` 2,4 ms (la primera llamada 47 ms por inicialización de libass);
  20 actualizaciones seguidas 56 ms (≈2,8 ms cada una, sobra para 10 Hz). Por IPC (`osd-overlay` desde Python, 35 554 chars): 45 ms
  incluyendo el primer render, respuesta `{"data":{},"error":"success"}`.
- Fusionando módulos consecutivos por fila (v3, URL real): 219 rectángulos y 6 797 caracteres en vez de 445 módulos oscuros.
- No hay límite documentado de tamaño de `data`; 48 kB funcionan sin aviso. Tamaño máximo razonable: **≤ 64 kB** por overlay (un QR v6 41x41
  fusionado queda muy por debajo, ~10–15 kB). Usar `{\bord0\shad0\blur0}` y un rectángulo blanco de fondo (zona de silencio de 4 módulos)
  antes de los módulos negros; `\an7\pos(x,y)` con coordenadas en PlayRes (res_x=0 → píxel cuadrado), sin depender de `osd-width`.

**Decisión:** pintar el QR con `mp.create_osd_overlay("ass-events")` + `mp.assdraw` desde el script Lua (mu-remote); sin ficheros temporales,
sin gestión de ids (los asigna mpv por cliente), z alto (p. ej. 1000) para quedar sobre uosc, y `remove()` al cerrar. `overlay-add` queda
como alternativa documentada solo si algún día hiciera falta un bitmap real.

## 3. Codificador QR disponible

`command -v`: `qrencode` NO · `zbarimg` NO · `segno` NO · `qr` NO · `python3` /usr/bin/python3. `.venv`: `segno` NO, `qrcode` NO, `PIL` NO,
`numpy` 2.5.3 sí. Python del sistema: sin `segno`/`qrcode`. Decodificadores (`cv2`, `pyzbar`, `zxing`): ninguno.

Fuentes offline encontradas (solo lectura):
- `/usr/lib/x86_64-linux-gnu/libqrencode.so.4` (paquete `libqrencode4` 4.1.1-2build1, dependencia de `libsystemd-shared`, así que está en
  prácticamente cualquier Debian/Ubuntu con systemd). Usable por `ctypes` sin instalar nada: `QRcode_encodeString8bit(str, version=0,
  level=1 /*M*/)` → struct `{int version; int width; unsigned char *data}` con el bit 0 de cada byte = módulo oscuro; liberar con `QRcode_free`.
- `/var/lib/flatpak/app/org.gnome.OCRFeeder/.../site-packages/reportlab/graphics/barcode/qrencoder.py` (MIT, port de qrcode.js de Kazuhiko
  Arase; 1130 líneas; solo importa `re` e `itertools`). Sirve de referencia para la implementación propia en Python puro.
- `/home/pc/Documentos/PROJECTES/CRYPTO-IA/saldada/node_modules/qrcode` (JS, no usado).

**Validación cruzada hecha** (`tmp/qr_check2.py`): para `http://192.168.1.50:8765/?t=abcdef12`, modo byte, nivel M, ambos eligen versión 3
(29x29); forzando la máscara 3 en qrencoder.py la matriz es **idéntica bit a bit** a la de libqrencode (0 módulos distintos). Solo difiere la
elección automática de máscara (criterio de penalización), que no afecta a la decodificación. Conclusión: las tablas de abajo son correctas y un
codificador en Python puro (v1–10, L/M, byte) es viable; el test unitario comparará contra libqrencode cuando esté (`ctypes`) y, si no, contra
vectores fijos generados aquí.

### Tablas copiadas literalmente de qrencoder.py (versiones 1–10, formato `[bloques, codewords_totales, codewords_datos, ...]`)

```
RS_BLOCK_TABLE (orden por versión: L, M, Q, H)
# 1
[1, 26, 19]  [1, 26, 16]  [1, 26, 13]  [1, 26, 9]
# 2
[1, 44, 34]  [1, 44, 28]  [1, 44, 22]  [1, 44, 16]
# 3
[1, 70, 55]  [1, 70, 44]  [2, 35, 17]  [2, 35, 13]
# 4
[1, 100, 80]  [2, 50, 32]  [2, 50, 24]  [4, 25, 9]
# 5
[1, 134, 108]  [2, 67, 43]  [2, 33, 15, 2, 34, 16]  [2, 33, 11, 2, 34, 12]
# 6
[2, 86, 68]  [4, 43, 27]  [4, 43, 19]  [4, 43, 15]
# 7
[2, 98, 78]  [4, 49, 31]  [2, 32, 14, 4, 33, 15]  [4, 39, 13, 1, 40, 14]
# 8
[2, 121, 97]  [2, 60, 38, 2, 61, 39]  [4, 40, 18, 2, 41, 19]  [4, 40, 14, 2, 41, 15]
# 9
[2, 146, 116]  [3, 58, 36, 2, 59, 37]  [4, 36, 16, 4, 37, 17]  [4, 36, 12, 4, 37, 13]
# 10
[2, 86, 68, 2, 87, 69]  [4, 69, 43, 1, 70, 44]  [6, 43, 19, 2, 44, 20]  [6, 43, 15, 2, 44, 16]
```

Capacidad derivada en modo byte (cap = (codewords_datos·8 − 4 bits de modo − bits de contador) / 8; contador 8 bits en v1–9, 16 en v10+):

| Versión | L: datos CW / bytes | M: datos CW / bytes |
|---|---|---|
| 1 | 19 / 17 | 16 / 14 |
| 2 | 34 / 32 | 28 / 26 |
| 3 | 55 / 53 | 44 / 42 |
| 4 | 80 / 78 | 64 / 62 |
| 5 | 108 / 106 | 86 / 84 |
| 6 | 136 / 134 | 108 / 106 |
| 7 | 156 / 154 | 124 / 122 |
| 8 | 194 / 192 | 154 / 152 |
| 9 | 232 / 230 | 182 / 180 |
| 10 | 274 / 271 | 216 / 213 |

(Coincide con ISO/IEC 18004 tabla 7. Una URL `http://192.168.1.135:PPPPP/?t=<token 16 hex>` tiene ≈45 bytes → v3-M o v4-M.)

```
GF(256) y polinomio generador (qrencoder.py):
EXP_TABLE = [x for x in range(256)]; LOG_TABLE = [x for x in range(256)]
for i in range(8):      EXP_TABLE[i] = 1 << i
for i in range(8, 256): EXP_TABLE[i] = EXP_TABLE[i-4] ^ EXP_TABLE[i-5] ^ EXP_TABLE[i-6] ^ EXP_TABLE[i-8]
for i in range(255):    LOG_TABLE[EXP_TABLE[i]] = i
# equivale al polinomio primitivo x^8+x^4+x^3+x^2+1 = 0x11D (EXP[8] = 0x1D)
def getErrorCorrectPolynomial(errorCorrectLength):
    a = QRPolynomial([1], 0)
    for i in range(errorCorrectLength): a = a.multiply(QRPolynomial([1, QRMath.gexp(i)], 0))
    return a                       # ∏ (x − α^i), i = 0..n−1
PAD0 = 0xEC; PAD1 = 0x11           # relleno alterno tras el terminador 0000
G15 = 0x537; G15_MASK = 0x5412     # BCH(15,5) de la información de formato + máscara
G18 = 0x1F25                       # BCH(18,6) de la información de versión (solo v≥7)
PATTERN_POSITION_TABLE (patrones de alineamiento) v1..v10:
[], [6,18], [6,22], [6,26], [6,30], [6,34], [6,22,38], [6,24,42], [6,26,46], [6,28,50]
Indicadores: nivel EC en bits de formato L=01, M=00, Q=11, H=10 (QRErrorCorrectLevel L=1, M=0, Q=3, H=2);
modo byte = 0100, contador de caracteres 8 bits (v1–9) / 16 bits (v10–26).
```

Nota: patrones de alineamiento copiados de `PATTERN_POSITION_TABLE` (índice version−1); la v10 `[6, 28, 50]` coincide con ISO 18004 anexo E.
Nuestra URL no pasa de v6, pero el codificador se limitará a v1–10 y el test comparará v1–10 contra libqrencode por `ctypes`.

## 4. Red

- IP LAN: `enp0s31f6` **192.168.1.135/24** (DHCP, gateway 192.168.1.1); `hostname -I` → `192.168.1.135`. Sin IPv6 global.
- Firewall: `ufw status` exige root. Pero `systemctl is-active ufw` → **active**, `/etc/ufw/ufw.conf` → `ENABLED=yes`,
  `/etc/default/ufw` → `DEFAULT_INPUT_POLICY="DROP"`. `/etc/ufw/user.rules` es 640 root, no legible. **Conclusión: un móvil de la LAN no podrá
  conectar hasta que Ser abra el puerto** (la prueba local pasó porque el tráfico a la propia IP entra por `lo`). Comando para NEEDS_HUMAN:
  `sudo ufw allow from 192.168.1.0/24 to any port <PUERTO> proto tcp comment 'mpv-uos remote'`.
- Puertos: **8765 está ocupado** por un proceso ajeno (`python3 -m http.server 8765 -d web`, pid 89709, lleva >1 día). mpvd no debe
  asumir 8765: elegir un puerto fijo distinto (propuesta 8790, configurable) y, si está ocupado, caer a uno efímero (`bind(("0.0.0.0",0))`).
  Puerto libre en la prueba: 48817. Otros escuchando: 631 (cups), 53 (resolved), 5939, 5435, 47831, [::1]:4173.
- `asyncio.start_server(handle, "0.0.0.0", puerto)` + cliente `urllib.request` con timeout 2 s: **OK** contra 192.168.1.135 (200 en 16 ms) y
  contra 127.0.0.1 (200 en 1 ms).

## 5. IP desde mpvd (Python) sin dependencias

```python
def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # UDP: no envía nada; solo elige la ruta/origen
        return s.getsockname()[0]
    except OSError:                        # sin ruta (sin red): fallback
        return "127.0.0.1"
    finally:
        s.close()
```
Probado: devuelve `192.168.1.135` con red; con la ruta por defecto ausente `connect()` lanza `OSError` (ENETUNREACH) y se devuelve
`127.0.0.1`. En Lua no hace falta: mpvd ya conoce la IP y el puerto y los manda al script (`remote.info`) para generar la URL del QR.
Alternativa de respaldo (`ip -4 -o addr`) solo como diagnóstico; no se usa en producción por no ser multiplataforma.
