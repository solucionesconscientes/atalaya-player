# El sitio web del proyecto (H50) · diseño

**<https://solucionesconscientes.es/atalaya>**

Dos páginas con dos trabajos distintos, y conviene no mezclarlos:

| | Para quién | Qué contesta | Cuánto se lee |
|---|---|---|---|
| **La pública** (`/atalaya`) | alguien que no conoce el proyecto | «¿por qué esto y no VLC?» | 30 segundos |
| **La exhaustiva** (GitHub) | quien va a usarlo en serio, a mirar el código o a desconfiar | «¿cómo está hecho y qué hace con mis datos?» | lo que haga falta |

---

## 1 · La página pública

### El error que hay que evitar
La tentación es listar las 60 funciones del BACKLOG. Eso no convence a nadie: quien llega ya tiene un reproductor
que le funciona, y una lista larga le dice «esto es complicado». La página tiene que contestar **una** pregunta —
¿qué hace esto que mi reproductor no hace? — y contestarla con cosas que de verdad no hace ningún otro.

### La frase de arriba
> **Atalaya Player** — Un reproductor que entiende lo que estás viendo. Subtítulos con IA en tu propio equipo,
> resúmenes con el minuto exacto, TV y radio, y una sala para ver juntos. Nada sale de tu ordenador.

Debajo, un botón de descarga y otro de «Ver cómo funciona» (al vídeo o a las capturas). Nada más en la primera
pantalla: ni menú de navegación largo, ni carrusel.

### Las cinco cosas, en este orden
Elegidas porque **ningún otro reproductor las tiene**, no porque sean las que más trabajo costaron:

1. **Subtítulos para cualquier vídeo, hechos en tu equipo.** whisper.cpp en local, con tiempos por palabra; te dice
   cuánto va a tardar *antes* de empezar, con números medidos en tu máquina. Traducción sin conexión (OPUS-MT) y
   subtítulos duales (original arriba, traducción abajo). *Nada sube a ninguna nube.*
2. **«¿Qué me he perdido?» y el índice del vídeo.** Te levantas a por agua, vuelves, y en un segundo tienes las
   frases que resumen lo que se dijo, cada una con su minuto: Enter y estás ahí. El índice hace lo mismo con la
   película entera, en secciones. En los vídeos de internet usa los subtítulos de la web: **cinco segundos**, no los
   ocho minutos que tardaría transcribir.
3. **Ver juntos, con un enlace.** Sin cuentas y sin servidor de nadie: se abre una sala en tu equipo y quien tenga el
   enlace ve lo mismo que tú, sincronizado, en su navegador. Y si lo que compartes es un archivo tuyo, el invitado
   puede **abrirlo en su propio reproductor** a calidad original: primer fotograma en 0,36 s y los saltos al
   instante, sin que tu equipo recomprima nada.
4. **TV y radio de verdad.** Miles de canales (TDTChannels, iptv-org, Radio Browser), guía de programación,
   **grabación programada** que despierta el equipo si está suspendido, y las emisoras caídas se saltan solas al
   siguiente espejo.
5. **Una sola puerta para abrir y descargar.** Pegas lo que sea —un enlace, veinte, una lista, un canal entero, una
   carpeta— y solo te pregunta una cosa: reproducir o descargar.

Cada una, en la página: **un título corto, dos frases, una captura**. Nada de párrafos.

### Y debajo, lo que da confianza
- **Qué hace con tus datos, en concreto.** No «respetamos tu privacidad», sino: los subtítulos se hacen en tu
  equipo; SponsorBlock pide los tramos por **un prefijo de 4 caracteres** del hash del vídeo, que vale para 1 de
  cada 65.536, y el filtrado se hace aquí, así que nadie sabe qué ves; los servicios de nube vienen **apagados**.
- **Sobre qué está hecho**: mpv y uosc, que son los que ya usan quienes saben. Esto no es un reproductor nuevo:
  es mpv con todo lo que le falta.
- **Qué necesita y qué no está probado todavía** (Windows y macOS funcionan, pero no se han probado en hardware
  real). Decirlo en la página pública, no esconderlo: quien se lo encuentre luego, se va.
- **Es libre y el código está ahí**, con el enlace a la página exhaustiva.

### Cómo debe estar hecha
- **Una sola página estática**, sin marco de JavaScript, sin cookies y sin analítica. Lo coherente con lo que el
  programa predica, y además carga al instante.
- Los colores de `brand.json` (`ink #0D1320`, `signal #3D7BFF`, `amber #FFB020`) y el logo «Anillo» que ya está en
  `docs/marca/`.
- **Capturas reales**, nunca maquetas: el menú, el panel de subtítulos con el tiempo estimado, el índice del vídeo
  con los minutos, la sala con el bloque «Abrir en mi reproductor» y la TV con la guía. Cinco, una por sección.
- Un **vídeo de 40 segundos** sin voz, con rótulos: abrir una película → `alt+c` subtítulos → `alt+R` resumen →
  `alt+W` sala. Si solo se puede hacer una cosa, esta es la que más convence.
- En **tres idiomas** (es/en/fr), como el programa.

---

## 2 · La página exhaustiva

### La recomendación que importa: **no la escribas dos veces**
La documentación exhaustiva **ya existe**: `docs/` son 18 documentos y `docs/DECISIONS.md` son **87 decisiones con su
porqué y sus medidas**. Escribir a mano una página paralela con «todos los detalles» garantiza que en tres meses diga
cosas que ya no son verdad, porque nadie actualiza dos sitios.

Así que: **publicar el repositorio en GitHub** y que la página exhaustiva sea **un índice navegable sobre lo que ya
está escrito** (GitHub Pages sobre `docs/`, o el propio README como portada). Lo único nuevo que hay que redactar es
lo que hoy no existe: un **mapa de la arquitectura** de una pantalla, que es justo lo que le falta a quien llega al
repositorio y ve 22 scripts y 266 métodos RPC.

### Lo que sí hay que escribir (y no existe): el mapa
- **Qué es y qué no.** No es un fork de mpv: es su configuración, 22 scripts Lua y un demonio. No-objetivos, con sus
  razones medidas: torrents, WebTorrent, un modo ligero.
- **Las dos piezas.** Los scripts Lua dentro de mpv, finos y que nunca bloquean; y **mpvd**, en Python con asyncio,
  que hace lo pesado (IA, red, índices, descargas) y habla por **JSON-RPC 2.0 sobre un socket Unix** (named pipe en
  Windows). Por qué así: mpv no tiene hilos ni sockets en Lua, y lo que tarda no puede estar en el hilo del vídeo.
- **El diagrama**: mpv ↔ (JSON IPC) ↔ mpvd ↔ (whisper.cpp · yt-dlp · ffmpeg · llama.cpp · OPUS-MT · SQLite).
- **Las tecnologías, con el por qué de cada una** (no solo la lista): mpv ≥ 0.41 y uosc ≥ 5.13 (sin fork, solo su
  API pública); Python 3.12 con uv; whisper.cpp para los subtítulos; OPUS-MT con CTranslate2 para traducir sin
  conexión; llama.cpp para el resumen en prosa; yt-dlp vendorizado y actualizado a diario; ffmpeg para grabar,
  convertir y las retransmisiones; SQLite para los índices y la caché; ONNX Runtime para la búsqueda semántica;
  qrcode propio y un servidor HTTP propio en vez de dependencias.
- **La caché**, que es lo que hace que todo parezca rápido: por `(hash del archivo, artefacto, modelo, versión,
  parámetros)`, así que lo que ya se calculó no se vuelve a calcular.
- **Privacidad, en detalle**: qué sale a la red, cuándo y con qué forma exacta.
- **Las medidas**, que son la cultura del proyecto: `docs/BENCHMARKS.md`, `BENCHMARKS_LLM.md` y los números de las
  decisiones (los 167 Mb/s de subida, el relay a 2,2× tiempo real, los 0,36 s del primer fotograma, el consumo
  frente a mpv pelado).
- **Cómo contribuir y cómo probarlo**: `tools/check.sh`, 779 tests, qué es sensible a la carga y por qué.
- **Estado por plataforma**, con lo que no está probado dicho sin rodeos.

### Dónde
GitHub, por tres razones: es donde mira quien va a desconfiar del código, da el historial y los ADR con fecha sin
trabajo extra, y las dos páginas quedan con dueños distintos (la pública vende, el repositorio demuestra). La página
pública enlaza al repositorio en un sitio visible pero no el primero.

---

## Lo que hace falta de Ser
1. **Las capturas y el vídeo** hay que hacerlos con el reproductor abierto en una pantalla real: no se pueden
   generar desde aquí.
2. **Decidir si el repositorio se hace público** (hoy no tiene ni remoto). Sin eso, la página exhaustiva no tiene
   dónde vivir y la pública no puede enlazar nada.
3. **Dónde se sirve** `/atalaya`: si es un subdirectorio del sitio que ya tienes, hace falta saber con qué está
   hecho para encajar la página.
