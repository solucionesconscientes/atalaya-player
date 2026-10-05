# Cómo está hecho Atalaya Player

Esto es el mapa que le falta a quien llega al repositorio y ve 23 scripts Lua y un montón de métodos RPC. No
repite lo que ya está escrito: las decisiones con su porqué y sus medidas están en [DECISIONS.md](DECISIONS.md),
el manual de uso en [USO.md](USO.md) y lo que no está probado fuera de Linux en
[PLATAFORMAS.md](PLATAFORMAS.md).

## Qué es y qué no es

**No es un fork de mpv.** Es la configuración de mpv, 23 scripts Lua propios y un servicio aparte en Python. mpv
se usa **tal y como lo trae el sistema**, y uosc se usa **por su API pública**, sin tocarle una línea. Eso tiene
una consecuencia que se nota: lo que mejora en mpv o en uosc llega aquí gratis, y lo que se rompe aquí no se
puede haber roto allí.

**Lo que no se hace, con su razón medida:**

| No-objetivo | Por qué |
|---|---|
| Torrents | Se construyeron y se quitaron (ADR-111): dependen de que haya pares, de que la red deje pasar DHT y de un extra que no viene de fábrica, y cuando falla cualquiera de las tres lo que se ve es «no funciona», indistinguible de un fallo nuestro. Para algo que no es crítico, el coste de soporte es desproporcionado. |
| Llevar mpv dentro de los paquetes | Rompe la aceleración por hardware, que depende de los drivers de la máquina (ADR-067, confirmado midiendo en ADR-115). |
| Un «modo ligero» aparte | Medido: no hay nada que apagar. La interfaz cuesta 1,2 % de un núcleo y los 23 scripts y el servicio cuestan 0,0 mientras se reproduce (ADR-115). |
| Actualizarse solo | Avisar sí, reemplazarse a sí mismo mientras está en marcha no: hecho mal, deja a alguien sin reproductor, y en Linux eso ya lo saben hacer apt y el gestor de AppImage (ADR-118). |
| Cuentas, nube, analítica | Local primero. Los servicios de nube opcionales vienen apagados. |

## Las dos piezas

```
   ┌─────────────────────────────── mpv (el del sistema, ≥ 0.41) ───────────────────────────────┐
   │  uosc 5.13 (interfaz)      23 scripts Lua «mu-*»: menús, TV, subtítulos, salas, notas…     │
   │                                   │  nunca bloquean el hilo del vídeo                      │
   └───────────────────────────────────┼───────────────────────────────────────────────────────┘
                                       │  JSON IPC de mpv (el propio socket del reproductor)
                                       ▼
   ┌──────────────────────────── mpvd (Python 3.12, asyncio) ────────────────────────────────────┐
   │  JSON-RPC 2.0 versionado sobre socket Unix (named pipe en Windows) · cola con prioridades   │
   │  whisper.cpp · OPUS-MT/CTranslate2 · llama.cpp · yt-dlp · ffmpeg · ONNX Runtime · SQLite    │
   └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Por qué dos piezas y no una.** El Lua de mpv no tiene hilos ni sockets, y corre en el mismo hilo que decide
cuándo sale el siguiente fotograma: cualquier cosa que tarde —transcribir, pedir algo a internet, leer un índice—
se vería como un tirón. Así que los scripts son finos (dibujan, escuchan teclas, piden cosas) y todo lo que tarda
vive en mpvd, que es un proceso aparte, con su propia cola y sus propias prioridades.

**Cómo se hablan.** Los scripts no abren sockets: mandan `script-message mu-rpc <petición>` y mpvd —que está
conectado al socket IPC de ESE mpv como un cliente más— contesta con `script-message-to <script> mu-reply`. Una
sola puerta, versionada, con `capabilities` obligatorio para que un script nuevo pueda hablar con un demonio
viejo sin romperse.

**Quién manda: la reproducción.** Mientras hay algo reproduciéndose, mpvd **no arranca nada especulativo**
(analizar la intro, pre-subtitular el siguiente episodio, indexar la biblioteca): eso espera a que se pause o se
pare, y entonces arranca solo. Lo que sí sigue es lo urgente (un subtítulo que va a salir ya) y lo que alguien ha
pulsado. Y lo pesado corre con `nice 10/15` y prioridad de disco en clase *idle*, que es la que importa cuando el
trabajo de fondo lee un archivo de varios GB mientras el reproductor lee otro (ADR-114).

## La caché, que es lo que hace que todo parezca rápido

Todo lo que se calcula se guarda por **(hash del archivo, artefacto, modelo, versión, parámetros)** en SQLite más
los ficheros grandes aparte. Dos consecuencias: lo que ya se calculó no se vuelve a calcular —abrir la misma
película otra vez es instantáneo— y cambiar de modelo o de parámetros no invalida lo demás, porque forma parte de
la clave. En desarrollo vive en `.cache/`; instalado, en las rutas que manda el sistema.

## Las tecnologías, y por qué cada una

| Pieza | Para qué | Por qué esta |
|---|---|---|
| **mpv ≥ 0.41** | reproducir | Es el que ya usa quien sabe: decodificación por hardware, filtros de FFmpeg, y una API que permite todo esto sin forkearlo. |
| **uosc ≥ 5.13** | la interfaz | Es la mejor interfaz que existe para mpv y tiene API pública para menús y botones: no hace falta escribir otra ni tocar la suya. |
| **Python 3.12 + uv** | mpvd | asyncio para hablar con todo a la vez sin hilos, y uv porque resuelve e instala en segundos y hace reproducible el entorno. |
| **whisper.cpp** | subtítulos en el equipo | Corre en una CPU normal, en C++, sin Python ni GPU, y da tiempos por palabra. Nada sube a ninguna nube. |
| **OPUS-MT + CTranslate2** | traducir sin conexión | Modelos pequeños por par de idiomas y un motor que los corre rápido en CPU. |
| **llama.cpp** | el resumen en prosa | Un modelo local pequeño basta para resumir lo que ya está transcrito, y así tampoco sale nada del equipo. |
| **yt-dlp** (vendorizado) | vídeos de internet | Es el que funciona. Va dentro y se actualiza a diario comprobando las sumas oficiales, porque las webs cambian más rápido que las distribuciones. |
| **ffmpeg** | grabar, convertir, retransmitir | Grabar copia el flujo tal cual (`-c copy`): un par de por ciento de un núcleo y ninguna pérdida. |
| **SQLite** | índices y caché | Un fichero, sin servidor, y transaccional: lo que hay que tener para una caché que sobrevive a cerrar el programa de golpe. |
| **ONNX Runtime** | búsqueda semántica | Buscar «cuando habla del perro» y encontrarlo aunque no se diga «perro», con un modelo de embeddings pequeño y local. |
| **Nada más** | — | El servidor HTTP de las salas y del mando, los códigos QR y el cliente IPC son propios: son pocas líneas y una dependencia menos que mantener, traducir y auditar. |

## Qué sale a la red, exactamente

| Cuándo | Qué sale | Qué NO sale |
|---|---|---|
| Subtítulos con IA, traducción, resumen, índice | **nada**: todo se calcula en el equipo | el audio, el vídeo, el texto |
| SponsorBlock | un **prefijo de 4 caracteres** del hash del vídeo (vale para 1 de cada 65.536) y el filtrado se hace aquí | qué vídeo se está viendo |
| TV, radio, guía | la petición a la lista o a la guía pública que toque | nada más |
| Vídeos de internet | lo que pide yt-dlp a ese sitio | nada a terceros |
| Salas y mando | solo a quien tenga el enlace, desde tu equipo; el túnel opcional solo si se enciende | nada a ningún servidor nuestro: no hay servidor nuestro |
| Avisos de versión | una petición al día al `latest.json` del sitio, solo en los paquetes | ninguna identificación: no hay identificadores ni analítica |

## Cómo se prueba

`tools/check.sh` lo pasa todo: lint de Lua (luacheck y además LuaJIT, porque luacheck da por bueno Lua moderno
que mpv rechaza), lint de shell, y los tests con pytest —unitarios y de integración con **mpv de verdad sin
ventana** (`--vo=null --ao=null`, socket IPC en `tmp/`), con el demonio de verdad y con servidores HTTP locales
en lugar de internet—. Lo que depende de la red va marcado y se puede omitir.

**Lo que hay que saber antes de echarle la culpa a un test:** unos pocos son sensibles a la carga de la máquina
(whisper tarda hasta 45 veces más con el equipo ocupado, y los que esperan a que un menú aparezca tienen plazos).
Si falla uno de esos, se repite aislado antes de tocar nada; si falla aislado, entonces sí es un fallo.

## Cómo colaborar

1. `uv sync && tools/vendor.sh` y `tools/check.sh` en verde antes de cualquier cambio.
2. Código y comentarios en inglés; documentación, menús y commits en castellano.
3. Una decisión que cambie cómo funciona algo va a `docs/DECISIONS.md` como un ADR corto, con **lo medido**: en
   este proyecto una afirmación sobre rendimiento sin un número al lado no vale. `tools/comparar.py` compara el
   gasto con el mpv de siempre y `tools/diagnostico.py` recoge lo que pasa mientras se ve algo.
4. uosc no se toca. Si hiciera falta, un parche documentado en `patches/` y dicho en un ADR.
