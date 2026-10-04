# PROGRESS
ESTADO_GLOBAL: EN_CURSO

## Resumen para Ser (2026-10-04, iteración 9) · H61-H66 y H49 idiomas (torrents, fuera)
De tus tres preguntas (minimizar no avisaba, cómo se ven los torrents, el mando de la tele en una Raspberry) y del
«hazlo todo de golpe, menos la web» salió esta tanda. Lo de la web (H50) sigue sin empezar, como pediste.

### H66 · Lo que encontraste al probarlo (2026-10-04)
- **Los torrents están fuera**, por tu decisión del 2026-10-04 (ADR-111). Se construyeron, no te funcionaron y se
  han quitado del todo —servicio, dependencia, puerta, ajustes, avisos, cadenas y test—: no eran críticos y eran
  lo único que dependía de que un torrent tuviera pares y de que la red dejara pasar DHT, así que fallaban de
  maneras que no se distinguen de un fallo nuestro. Queda escrito lo medido, por si algún día se retoma.
- **Una franja programada abre su propia ventana y la maximiza**, y la cierra al acabar: si a las 21:00 estás
  viendo una película, «música de 21:00 a 23:00» ya no te la quita.
- **Se pueden programar archivos y carpetas del equipo**, no solo canales, listas de Música o lo que estuviera
  puesto: se exploran las carpetas con las flechas, Enter programa ese archivo, `Tab` lo añade a la selección y
  los marcados van juntos. Para el mando del televisor, «toda esta carpeta» es una fila, no una acción de `Tab`.

### H61 · El mando del televisor (HDMI-CEC)
Pensando en la Raspberry enchufada a la tele: si el aparato lo permite, las flechas, OK, play/pausa y los números
del **mando del televisor** manejan el reproductor, sin teclado. Se habla con `/dev/cec0` del kernel directamente
(sin libcec ni paquetes del sistema) y funciona también fuera del modo salón. **No lo he podido probar con una tele
de verdad** —no hay ninguna aquí—: está escrito contra la API del kernel y probado con un dispositivo falso; en
`NEEDS_HUMAN.md` queda la orden exacta para comprobarlo cuando haya una.

### H62 · Lo que pasa por detrás se ve y se dice
Guardar los cortes de un vídeo **no recodifica nada** (se copian las pistas: un corte de 10 minutos son dos
segundos), pero antes solo se avisaba al empezar. Ahora: las tareas que tardan salen en **Tareas** (alt+j) con su
nombre en claro, y al terminar sale un aviso del escritorio si han tardado más de 20 segundos. Las que son
instantáneas no molestan.

### H64 · Explorar las carpetas del equipo
**Biblioteca › Explorar** recorre el equipo entero con el mando: discos y sitios (Vídeos, Música, Descargas…),
subir, **reproducir la carpeta entera**, añadirla a la biblioteca. Para la tele sin teclado, que era la idea.

### H63 y H65 · Tests que no fallan al azar, y un menú que no se reabre
Seis fallos distintos que parecían «sensibles a la carga» eran de verdad: las filas de la vista anterior se leían
como si fueran de la nueva (arreglado en los trece scripts, no en los tests) y un menú cerrado se volvía a abrir
solo cuando llegaba una respuesta tardía.

### H49 · El programa en tres idiomas
Castellano, inglés y francés: **1.533 cadenas por idioma** (3.066 traducciones), ninguna vacía. Si el sistema está
en castellano o francés, ese; en cualquier otro, inglés; y en **Preferencias › Idioma** se puede forzar. Lo que no
está traducido cae al castellano, así que el peor caso es «se ve en español», nunca un hueco.
- Las páginas que sirve el programa (la sala de *ver juntos*, el mando del móvil, el panel de descargas) siguen al
  **navegador del invitado**, no a tu sistema: las abre otra persona y puede estar en otro idioma.
- Al repasar las traducciones salió algo que no era de traducción: la pista «también **mañana** 9:00 1h30» de
  *Programar* tenía que seguir diciendo una palabra castellana, porque el programa solo entendía `hoy`/`mañana`/
  `pasado`. Ahora entiende también `today`/`tomorrow` y `aujourd’hui`/`demain`/`après-demain` (y las castellanas
  siguen valiendo siempre).
- Quedan medidas **491 cadenas** de mpvd que el extractor no veía porque no son `RpcError` (los formatos de
  «Convertir», los modelos de voz, los nombres de las tareas…): está anotado como H49/G8 con el detalle por
  ficheros. No es un error nuevo: es la parte que faltaba por descubrir, y ahora se sabe cuánta es.
- Y una trampa que costó tres fallos y ahora la vigilan dos tests: la función de traducción se llama `t()`, que es
  también el nombre que cualquiera le pone a una variable temporal. Cuando eso pasa, en JavaScript la página se
  queda muda y en Python el mensaje de error se convierte en otro error (`UnboundLocalError`) justo cuando hay un
  error que contar (ADR-109).

Probar a mano:
```bash
bin/mpv-uos                      # ctrl+o y pega un magnet → «Ver mientras se descarga» (enciéndelos antes en Preferencias)
bin/mpv-uos                      # alt+j → Biblioteca › Explorar: discos, carpetas, «reproducir toda esta carpeta»
MPV_UOS_LANG=fr bin/mpv-uos      # todo en francés, incluido el menú y los avisos
MPV_UOS_LANG=en bin/mpv-uos      # y en inglés; el mando del móvil, en el idioma del móvil
```

## Resumen para Ser (2026-10-03, iteración 8) · H58, H60, J5, H44/C8, H53, K3
De tu tercera prueba y del repaso de `docs/IDEAS.md` salieron cuatro cosas, y están. Detrás van las cinco que
aprobaste después.

### J5 · Programar una lista de música
Faltaba la otra mitad de lo que pediste en la prueba anterior («programar canciones o playlist»): la franja ya
existía, pero desde el menú solo se podían elegir **canales**. Ahora en *Programar* hay dos puertas más: **«Una
lista guardada…»**, que enseña las listas de Música, y **«Lo que está puesto ahora»**. La lista se encola entera y
**se repite hasta que acabe la franja** —si no, «música de 21:00 a 23:00» con una lista de veinte minutos se
acabaría a y veinte— y al terminar se deja la repetición como estuviera. El título del menú dice «Poner», no
«Grabar», porque grabar algo que ya está en el disco no tiene sentido.

### H64 · Explorar las carpetas del equipo
Lo pediste pensando en la Raspberry conectada a la tele, y ese detalle manda en todo: lo único que había para
elegir una carpeta era **teclear la ruta**, y en un salón no hay teclado. Ahora se navega con las flechas y Enter,
desde *Biblioteca*, desde *Carpetas* y desde la puerta única con la caja vacía. Se empieza por tus carpetas, las de
la biblioteca, **las unidades conectadas** —un pincho USB en la Pi sale solo, que es donde van a estar las
películas— y la carpeta personal. Dentro, las subcarpetas primero y en orden natural («Capítulo 2» antes que
«Capítulo 10») con cuántas cosas tienen dentro; los archivos con su tamaño.

Dos cosas que **no** se han construido, y es lo que hace que esto sea pequeño: reproducir una carpeta entera no
lleva ni una línea, porque **mpv abre directorios él mismo** y monta la lista; y no hay índice ninguno, porque para
indexar ya está la biblioteca y un navegador tiene que contestar al instante. Y la forma la decide el mando y no el
teclado: lo que se puede hacer con una carpeta son **filas** —el mando de la tele no tiene Tab— y hay una fila
**«Subir»**, porque en ese mando «atrás» cierra el menú y no sube un nivel.

### H62 · Ver y saber lo que pasa por detrás
Tres cosas de tu prueba. **Guardar un tramo ya no recodifica**: el formato de fábrica era MP4 —o sea recodificar— y
tú daba por supuesto lo contrario, con razón, porque cortar es copiar los flujos (0,07 s frente a 1,16 s, y sin
perder un bit). Unir varios en uno sí obliga, porque pegar trozos es un filtro, y eso lo dice la lista antes de que
pulses. **«Tareas» enseña ahora TODO** lo que pasa por detrás —subtítulos con IA, traducción, índice por temas,
intro, música…—, que ya pasaba por la misma cola del servidor pero no se publicaba; con el nombre en castellano y
sin anunciar lo que dura menos de dos segundos, para que el icono no parpadee y acabes ignorándolo. Y **avisa al
terminar** con un aviso del escritorio, que es lo que se ve con el reproductor detrás o cerrado: solo si ha tardado
más de 20 s, y siempre si ha fallado.

### H61 · El mando del televisor en la Raspberry
Dijiste «podemos hacerlo y que luego sea lo que Dios quiera», y está hecho. Lo que cambió al construirlo: en vez de
leer la salida de texto de `cec-client` —cuyo formato no puedo comprobar sin tenerlo instalado, y adivinar formatos
es lo que este proyecto no hace— lee **el aparato del kernel** (`/dev/cec0`), que es una interfaz binaria
documentada, no necesita instalar nada en la Pi y tiene la misma forma que el joystick que ya lee el gamepad. Las
teclas se traducen a las mismas acciones del gamepad, así que no hay un segundo mapa que mantener, y **manda
siempre**, no solo en modo salón: un mando físico que no hace nada al pulsarlo es el fallo silencioso de siempre.
Probado de punta a punta dándole al demonio los mismos `struct cec_msg` que daría el kernel: play, pausa, avanzar,
volumen, abrir el menú y cerrarlo. **Falta la última milla**, que tu televisor pase sus teclas, y eso solo se ve en
la Pi: las órdenes para comprobarlo están en NEEDS_HUMAN.md.

### K3 · Volumen parejo y temporizador, donde se buscan
Aquí el trabajo no era construir nada: ya estaba. El volumen parejo funcionaba (etiquetas ReplayGain por pista o por
álbum, y para música sin etiquetas la ganancia que mide el daemon) pero **solo se ofrecía en Música**, que es el
último sitio donde uno mira viendo una película; y el temporizador para dormirse ya valía para cualquier
reproducción, pero vivía dentro del menú de audiolibros. Ahora las **dos formas de igualar el volumen van juntas**
en *Imagen y sonido*, cada una diciendo lo que cuesta —las etiquetas son gratis pero solo donde las hay; el
nivelador vale para todo a cambio de algo de CPU—, y el **temporizador tiene puerta propia** en el menú principal y
en la paleta. No se han unificado en un interruptor «listo» a propósito: elegir solo por fichero haría que el
sonido cambiara de carácter entre canciones, y eso desconcierta más de lo que ayuda.

### Lo que NO se ha hecho, y por qué
- **El remux para el navegador (H44/C8)**: descartado al medirlo. La nota que lo justificaba decía «en vez del relay
  recodificando», y el relay **ya** copia cuando los códecs lo permiten: para el mismo fichero, remux 0,08 s y relay
  0,08 s. Donde sí recodifica —HEVC + Opus, que es lo que graba esta casa— un remux no sirve, porque eso no lo abre
  ningún navegador; para eso está desde H44 el «ábrelo en tu reproductor», con el fichero original.

### H60 · El vídeo se quita solo al minimizar
Preguntaste si minimizar ya deja de decodificar y si entonces el botón de apagar el vídeo es redundante. Lo medí:
**minimizar no para nada** (37 % de un núcleo con la ventana visible, 18 % minimizada, 8 % sin vídeo, y los mismos
fotogramas descartados). Lo que ahorra es pintar. Así que ahora **se quita solo** mientras la ventana está escondida
y vuelve al restaurarla, encendido de fábrica. En un vídeo de internet, además, **deja de descargarse**: la descarga
baja al 35 % y tarda 0,03 s, sin recargar nada — con lo que se cayó la parte arriesgada del plan, que era tocar la
selección de formatos. Y el botón: **ya no estaba en la barra** (lo quité en H51 para hacer sitio a tramos y notas,
quedó escrito en docs/INTERFAZ.md pero no te lo dije). Queda `alt+a` para el caso raro de escuchar con la ventana
delante, y **ya no se recuerda**: todo vídeo se abre con su imagen.

- **Se ve lo que pasa por detrás.** Tu «guardar los tramos no hace nada» era **falso** —los archivos se creaban—
  pero no había ninguna señal: iban a una carpeta que nadie había visto y, al unir, se recodificaba en silencio.
  Desde fuera eso es idéntico a estar roto. Ahora, mientras hay trabajo, en la barra aparece un icono con **el
  número de tareas**, el nombre de la que corre y su porcentaje; al pulsarlo se abre Tareas. Sin trabajo, no está.
- **Se avisa antes de pulsar, no después.** Queda escrito como regla en `docs/INTERFAZ.md`: una opción que no puede
  funcionar sale apagada, diciendo por qué y señalando lo que sí sirve. Aplicado en tramos (un vídeo de internet o
  la TV no se pueden cortar), en los mandos del invitado y en «ir a un minuto».
- **Ir a un minuto escribiéndolo** (`g`, o el icono de la barra): admite `2:15`, `1:02:15`, segundos sueltos y
  relativos (`+30`, `−30`), que es lo que se escribe de verdad.
- **La charla de una hora en quince minutos.** Elige los pasajes que mejor la representan, cortados por frases
  enteras, y los reproduce seguidos **sin generar ningún archivo** (línea de tiempo virtual de mpv), al instante.
  Guardarlo en un archivo sigue estando, pero es otro paso porque eso sí cuesta. Pedidos 15 min → 14,9 reales.
- De paso, un fallo real que llevaba tiempo: **«Continuar viendo» contaba dos veces** la misma reproducción. No era
  de esta tanda; los reintentos no eran idempotentes y la fila la podía crear un guardado de posición.

- Probar a mano:
  ```bash
  bin/mpv-uos pelicula.mkv      # g → 00:02:15 · menú › Resumen e índice › Verlo acortado
  bin/mpv-uos conferencia.mp4   # tramos (barra) → guardar: el icono de Tareas aparece mientras trabaja
  ```
- `tools/check.sh` al cerrar la tanda: **863 pasan**, 2 omitidos, 16 con red. En las dos pasadas completas falló un
  test distinto por la **misma carrera** —el estado publica la vista antes que sus filas, así que esperar el nombre
  de la vista devuelve las filas de la anterior en cuanto la máquina va cargada—: `test_books` en la primera y
  `test_mu_subs` en la segunda. Los dos pasan aislados y en los dos se ha arreglado la causa, no el síntoma: ahora
  esperan una fila de la vista nueva. Es la tercera vez que esta carrera muerde, y ya está escrita abajo.

## Resumen para Ser (2026-10-02, iteración 7) · H42-H46
De tu segunda prueba salieron cinco hitos. **Están los cinco**, menos una pieza del de la sala que digo al final.

- **H42 · Una sola puerta para abrir y descargar.** Antes había seis sitios para lo mismo y tenías que saber de
  antemano qué ibas a pegar. Ahora hay **un campo** (`ctrl+o`, o la primera fila del menú) donde cabe lo que sea: un
  enlace, veinte, una lista, un canal entero, la ruta de un archivo o de una carpeta, un `.txt` con enlaces o, si lo
  dejas en blanco, lo que tengas copiado. Te dice **cuántas cosas** hay y hace **una sola pregunta: reproducir o
  descargar**. Si eliges descargar, caes en la pantalla de siempre. Y todo enlace que haya que llevarse a otro
  aparato (la sala, el mando del móvil, el panel de descargas) tiene su botón de copiar.
- **H43 · Grabar: ahora eliges el formato.** Una fila, y se recuerda: *igual que el original* (MKV, el que nunca
  falla), *MP4 si los códecs lo permiten* —la fila te dice si los de ese vídeo caben, y si no caben te avisa y graba
  en MKV en vez de dejarte un fichero roto— o *solo el audio*. **Programar una grabación** ya no está escondido
  detrás de un Tab: está en *TV y radio* y en *Grabar*. Y **las emisoras de radio también se pueden programar**.
- **H45 · El resumen ya funciona en los vídeos de internet**, que es lo que echabas en falta. No estaba roto: se
  quedaba sin palabras, porque un vídeo de YouTube no trae subtítulos cargados. Ahora, al pulsar, se piden los que
  ofrece la web: **cinco segundos**, frente a los ocho minutos que tardaría transcribirlo. Y deja de estar
  escondido: **«Resumen e índice»** está en la raíz del menú (cuando hay algo que ver) y en el panel de subtítulos.
- **H44 · La sala.** Tu queja era que el vídeo tardaba mucho desde el móvil: la retransmisión **empezaba en el
  segundo 0** de la película y tú ibas por el minuto 40, así que el invitado esperaba a que el empaquetado llegara
  hasta ti. Eran **~18 minutos**. Ahora empieza donde vas tú y lo dice. Además, si lo que compartes es un archivo de
  tu equipo, el invitado puede **abrirlo en su propio reproductor** (copiar el enlace, bajar un `.m3u` o pegar una
  línea en un terminal) y lo ve **como es**, sin recomprimir nada: primer fotograma en 0,36 s y saltos en 0,07 s. De
  paso se arregló un fallo real: el servidor cargaba el fichero **entero en memoria** por cada petición (una película
  de 4 GB habrían sido 4 GB).
- **H44/C6 · y ahora el invitado puede entrar desde Atalaya Player, no solo desde el navegador.** Es la forma buena
  de ver juntos: le llega **tu archivo original** (calidad original, cualquier códec, saltos instantáneos, tu equipo
  sin recomprimir nada) y su reproductor **sigue solo** tus pausas, tus saltos y tu velocidad. Si se separa unas
  décimas, va un poco más rápido o más lento hasta ponerse a la par, sin que se note; si la diferencia es grande,
  salta. Para entrar le vale el mismo enlace de siempre, por donde quiera: pegándolo en *Abrir o descargar*, en
  *Compartir → Entrar en una sala de otro…*, o pasándoselo al programa (`mpv-uos "<enlace>"`), que abre el
  reproductor ya dentro de la sala.
- **H46 · Un solo menú.** Había un segundo menú escondido en `ctrl+m` con **119 entradas** y cuatro cosas repetidas.
  Fuera. Antes de quitarlo se revisó entrada por entrada: dos cosas vivían solo allí (*repetir la lista* y *orden
  aleatorio*) y se trajeron al menú bueno.

### Lo que falta y por qué
- **El nombre (H41)**: decidido por ti, **Atalaya Player**. Está en `brand.json` y en ningún otro sitio.
- **H44/C8** (nuevo, menor): remux `-c copy` en vez de recodificar para el navegador. Ahorraría CPU de tu equipo
  (9,64 s para 115 min frente a 2,2× tiempo real), pero ya no arregla nada roto: con C5 la espera desapareció.

### Cómo probar lo de esta iteración a mano
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
mpv-uos                                   # ctrl+o: pega un enlace, dos, una lista o una ruta → reproducir o descargar
mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+R resumen (pide los subtítulos de la web) · alt+I índice
mpv-uos tests/fixtures/media/video30.mkv  # alt+r graba · en «Grabar» cambia Formato a MP4 y vuelve a grabar
mpv-uos                                   # alt+t → «Programar una grabación…» y elige una RADIO
mpv-uos ~/Vídeos/alguna-pelicula.mkv      # alt+W crea la sala; en el móvil, «Abrir en mi reproductor»
alt+m                                     # el menú: ocho categorías; ctrl+m ya no abre nada

# entrar en una sala desde el propio reproductor (H44/C6), con DOS ventanas en este mismo equipo:
mpv-uos ~/Vídeos/alguna-pelicula.mkv      # ventana 1: alt+W → crear la sala → «Copiar el enlace»
mpv-uos "<el enlace copiado>"             # ventana 2: abre ya dentro; pausa/salta en la 1 y mira la 2
#   (o en la ventana 2: ctrl+o y pegar el enlace → «Entrar en esa sala»)
```

## Resumen final para Ser (2026-10-01, iteración 6)
Todo el BACKLOG está hecho salvo **el nombre, que decides tú** (H41). De tu prueba real salieron siete hitos nuevos y
están los seis que no dependían de ti:
- **H35**: el botón ● para la grabación en el segundo clic, el QR de la sala se puede quitar (y `alt+Q` lo pone y lo
  saca), el anfitrión ve cómo va la retransmisión, y la barra de abajo tiene botón de subtítulos y de «solo audio».
- **H36**: fuera los subtítulos IA en vivo. Se prepara el archivo entero con el modelo bueno y **se te dice cuánto va a
  tardar con números medidos** («listos en 7 min · no te alcanzará»). Al cerrar, se te pregunta qué hacer con lo que
  quede trabajando. Buscar subtítulos ya no es un callejón sin salida: te guía para la clave en dos pasos. Traducir pasa
  a máxima calidad (OPUS-MT, con francés añadido). Y el panel va en tres bloques, con el tamaño y el retraso a mano.
- **H37**: una sola puerta para descargar. Pegas un enlace, veinte, una lista, un canal o un `.txt` y sale una lista con
  casillas, formato para todos y formato por fila. Lo que yt-dlp **no puede** (los guardados de Instagram) se te dice
  antes de intentarlo, con la alternativa.
- **H38**: `alt+I` da el **índice del vídeo** (secciones y frases clave, cada una a su minuto, al instante) y, al final,
  un **resumen en prosa** escrito por un modelo que corre en tu equipo, con el tiempo dicho antes de empezar.
- **H39**: las radios que no suenan pasan solas al siguiente espejo (era tu caso), la lista dice lo que dijo la
  comprobación, y **SponsorBlock funciona al ver**, no solo al descargar, sin decirle a nadie qué estás viendo.
- **H40**: despertar el equipo para grabar y suspender o apagar al terminar, con tres seguros y un aviso cancelable.
Lo que necesita algo de ti está en NEEDS_HUMAN.md (y es poco): una orden con `sudo` para el despertador, tu cuenta de
OpenSubtitles para poder **descargar** subtítulos, y —si quieres— una clave gratuita de Subdl.
Lo que no se puede probar sin hardware (Windows, Mac, Raspberry Pi, tele DLNA real) está hecho y probado aquí por
simulación, con los pasos exactos para ti en NEEDS_HUMAN.md.

### Qué funciona
- **Reproductor**: mpv 0.41 + uosc 5.13, config portable, un único menú con migas y «‹ Atrás» (`alt+m`), paleta (`alt+p`),
  pantalla de inicio, continuar viendo por hash, preferencias que se recuerdan, mini reproductor, modo salón y modo sencillo.
- **TV y radio** (`alt+t`): TDTChannels, iptv-org, Radio Browser y tus M3U; buscar en cada lista, CC/VO/AD, guía de TV (`alt+G`)
  y grabación programada.
- **Internet** (`ctrl+u`, `ctrl+f`, `alt+y`): YouTube y otras webs con yt-dlp (nightly de reserva), calidad con «fluido en tu equipo»,
  descargas avanzadas (listas con casillas, subtítulos SRT, cola que sobrevive), convertir (`alt+C`), tareas (`alt+T`),
  suscripciones (`alt+Y`), panel web de descargas y «Enviar a MPV-UOS» desde el navegador.
- **Subtítulos**: IA en local (whisper.cpp) con tiempos por palabra, subtítulos de la web, traducción offline, duales,
  resincronizar, guardar SRT (`alt+S`), subtítulos de internet por hash (desactivado por defecto).
- **Audio**: música (`alt+M`: biblioteca, listas, cola, sin cortes, ReplayGain, perfiles de auriculares), letras (`alt+K`),
  audiolibros y podcasts (`alt+A`), solo audio para todo (`alt+a`), ecualizador y MPRIS.
- **Más**: saltar intro/créditos (`alt+k`), búsqueda semántica y capítulos, estudio y «Mis notas» (`alt+B`), grabar (`alt+r`),
  biblioteca (`ctrl+b`), mando QR (`alt+z`), compartir salas y emitir en directo (`alt+W`), enviar a la tele DLNA (`alt+E`),
  «¿Qué me he perdido?» (`alt+R`), MCP para asistentes, logo «Anillo».
- **Plataformas**: instalador Linux sin sudo, AppImage x86_64, `.app` de macOS, lanzador e instalador de Windows (PowerShell) y
  mpvd por named pipes (Windows y Mac sin probar en real).

### Cómo probarlo (comandos exactos)
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/check.sh                                     # todo, con el portátil libre (Whisper y MPRIS fallan por tiempo bajo carga)
tools/install.sh --extras                          # instala mpv-uos en ~/.local/bin y en el menú de aplicaciones
mpv-uos                                            # inicio · alt+m menú · alt+p paleta · alt+t TV · alt+M música · ctrl+b biblioteca
mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+a solo audio · alt+q calidad · alt+d descargar · alt+T tareas
mpv-uos tests/fixtures/media/voz_es_en.mkv         # alt+i subtítulos IA · alt+S guardar SRT · alt+e estudio · alt+B notas
tools/build_appimage.sh && dist/MPV-UOS-x86_64.AppImage   # AppImage
.cache/pwsh/pwsh -File bin/mpv-uos.ps1 -DryRun video.mkv  # lanzador de Windows (orden que ejecutaría)
```
Lo arreglado en H34, a mano (iteración 5): `alt+b` una nota y escribe un párrafo en su `.md` (no se pierde); una película
con VO + doblaje, `alt+c` en cada pista (subtítulos distintos); encola tres descargas con «descargas a la vez» en 1 y
cancela la última (queda «cancelada», no «en cola»); *Estado de yt-dlp → Instalar*; graba un tramo de más de 10 minutos;
graba un canal de TV desde `alt+t` y párala con `alt+r` (el punto rojo se va); `alt+h` y Esc antes de que responda mpvd
(no reaparece el menú); `alt+p` y `⌫` (cierra).
Cada hito tiene sus pasos a mano en «Registro por iteración».

### Qué quedó pendiente o bloqueado (y por qué)
- ~~Túnel de internet de las salas~~ (H25): **hecho** el 2026-10-01 (ADR-068). Apagado por defecto: se enciende en
  *Compartir → Que se pueda entrar desde internet*.
- **Torrents** (H26 [~]): fuera por tu decisión del 2026-09-30.
- **Probar en hardware que no hay aquí**: Windows, macOS, Raspberry Pi 5, tele DLNA real (pasos en NEEDS_HUMAN.md).
- **Cortafuegos**: `ufw` bloquea la entrada; mando (8790), salas (8791) y DLNA (8792) necesitan un `sudo ufw allow …` (NEEDS_HUMAN.md).
- **Nombre de la app**: pendiente de tu decisión (centralizado en `brand.json`, que leen `mpvd/brand.py` y `mu/brand.lua`).
- **Tests sensibles a la carga**: los de Whisper (`test_asr_engine`, `test_mu_subs`) y, cuando la carga es alta, también
  `test_nav` (maneja menús de uosc con pulsaciones de tecla) fallan por tiempo si el portátil está ocupado; aislados y con
  la máquina libre pasan. Última pasada completa (2026-10-01, con tu `llama-server` en ~3,5 de 4 núcleos): **716 pasan, 3
  fallan**, y los tres son esos; `test_nav` repetido aislado pasa sus 16.
  El 2026-10-01 tu `llama-server` ocupaba ~3,5 de los 4 núcleos (whisper medido a `rtf 2.1`, cuando libre va a ~0,3), así
  que la última pasada completa de `tools/check.sh` no es concluyente para ellos: repítela con el portátil libre.
- ~~`test_mpris`~~: **arreglado** (era el test, no el reproductor): su ayudante tiraba a la basura justo la señal que
  esperaba, porque `recv_until_filtered` de jeepney extrae y devuelve el mensaje y el test ignoraba ese valor.

## Resumen para Ser (2026-09-29, histórico)
Todos los hitos H0–H13 de BACKLOG.md están [x]; ninguno quedó [~]. `tools/check.sh` pasa 202 tests sin red + 4 con red; lo único
sensible es la CPU: los tests de Whisper (`test_asr_engine`, `test_mu_subs`) fallan por tiempo si el portátil está ocupado con otros
trabajos (ver "Qué quedó pendiente").

### Qué funciona
- **Reproductor**: mpv 0.41 + uosc 5.13 con configuración portable (`mpv-config/`, nunca `~/.config/mpv`), menú MPV-UOS (`alt+m`,
  botón derecho), paleta global (`alt+p`), pantalla de inicio y "continuar viendo" por hash del contenido.
- **mpvd** (daemon Python sin dependencias obligatorias): JSON-RPC 2.0 con `capabilities`, sesiones por IPC, caché SQLite por hash,
  cola con prioridades y guardián de rendimiento; lo arranca mu-core solo.
- **TV y radio**: TDTChannels, iptv-org, Radio Browser y M3U propias; búsqueda sin acentos, favoritos, recientes, zapping, ICY y grabación.
- **yt-dlp**: binario vendorizado con actualización diaria verificada, vídeo/solo audio en caliente, menú de todos los formatos,
  descargas con presets (vídeo, audio original, MP3/Opus/M4A/FLAC/WAV con bitrate), cola con progreso.
- **Subtítulos IA** en vivo (whisper.cpp, look-ahead, caché reanudable, pre-subtitulado del siguiente), resincronización, traducción
  offline (Argos/CTranslate2) y subtítulos duales.
- **Sonido e imagen**: diálogo claro, modo noche, RNNoise, binaural, fotosensible, perfil ligero, diagnóstico de tirones.
- **MCP** para asistentes (Claude Code), **intro/créditos** por huellas de audio, **búsqueda semántica** y capítulos por tema,
  **modo estudio** (repetir línea, velocidad inteligente, notas Markdown, clips/GIF) y **mando QR/PWA** desde el móvil.
- **Instalación de usuario** en Linux sin sudo (`tools/install.sh`), guía `docs/USO.md`, teclas `docs/ATAJOS.md`, plataformas
  `docs/PLATAFORMAS.md`, decisiones ADR-001…032 en `docs/DECISIONS.md`.

### Cómo probarlo (comandos exactos)
```bash
cd ~/Documentos/PROJECTES/MPV-UOS
tools/check.sh                                   # todo (genera tests/fixtures/media); mejor con el portátil libre (≈7 min)
tools/install.sh --extras                        # instala mpv-uos en ~/.local/bin y "MPV-UOS" en el menú de aplicaciones
mpv-uos                                          # pantalla de inicio · alt+m menú · alt+p paleta
mpv-uos tests/fixtures/media/voz_es_en.mkv       # alt+c subtítulos IA · alt+i menú de subtítulos · alt+e estudio
mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+a solo audio · alt+q calidad · alt+d descargar · alt+l descargas
mpv-uos                                          # alt+t TV y radio · alt+f buscar canal · alt+↑/↓ zapping · alt+r grabar
mpv-uos tests/fixtures/media/serie/ep02.mkv      # intro detectada: alt+k salta · alt+j menú
mpv-uos tests/fixtures/media/chapters.mkv        # alt+z QR para el móvil (antes: sudo ufw allow … 8790, ver NEEDS_HUMAN.md)
.venv/bin/python -m mpvd status                  # estado del daemon
```
Cada hito tiene sus pasos a mano detallados más abajo, en "Registro por iteración".

### Qué quedó pendiente o bloqueado (y por qué)
- **Cortafuegos** (NEEDS_HUMAN.md): `ufw` bloquea la entrada, así que el móvil no llega al mando hasta ejecutar el `sudo ufw allow …`.
- **Notion**: en esta sesión el conector de Notion no estaba autorizado (hay que autorizarlo en los ajustes de conectores de claude.ai),
  así que la Bitácora no recoge H12–H13; las etiquetas de Stack siguen pendientes (NEEDS_HUMAN.md).
- **Windows**: falta el lanzador PowerShell y el transporte de mpvd por named pipe; **macOS** sin probar (tabla en docs/PLATAFORMAS.md).
- **Subtítulos IA en URLs/directos**: solo archivos locales (grabar el audio de la URL con ffmpeg está por hacer).
- **Tests de Whisper y carga**: con otros procesos pesados en el portátil (load > 8), `whisper-cli` va hasta 45× más lento y esos dos
  tests fallan por tiempo; con la máquina libre pasan. Repetir: `MU_KEEP_LOGS=1 uv run pytest tests/test_asr_engine.py tests/test_mu_subs.py`.
- **Ideas siguientes** del TOP 10 de docs/VISION.md aún sin hito: "¿qué me he perdido?" (B11), OCR de subtítulos PGS (B6),
  diccionario/Anki (C2–C3), handoff entre dispositivos (E5), MPRIS/KDE Connect (E4), supercut y resumen elástico (I1, I5).

## SIGUIENTE PASO
El 2026-10-04 se hizo de una tanda lo que quedaba aprobado menos la web: **H61** (mando del televisor), **H62**,
**H63**, **H64**, **H65** y **H49/G1-G7** (los tres idiomas); y después, de su primera prueba, **H66** (la franja
en su propia ventana maximizada y programar archivos del equipo). **Los torrents (H59) se quitaron** el mismo día
por decisión de Ser, al no funcionarle y no ser críticos (ADR-111).

Lo que queda, por orden:

1. **Que Ser vuelva a probar**, ahora con todo construido y traducido. Es lo único que puede desbloquear lo demás.
2. **Arreglar lo que salga** — y si algo no es crítico y no funciona, **quitarlo**: es lo que se hizo con los
   torrents el 2026-10-04 y es el criterio para lanzar.
3. **H63/N3 · el fallo intermitente de los menús**: cuando una vista pinta dos veces y la segunda llega antes de
   que uosc publique su tipo, uosc cierra el primer menú y el script toma ese cierre por uno de la persona y se
   queda sin vista (⌫ salta de nivel, lo que cargaba no aparece, un panel deja de refrescarse). La causa está
   diagnosticada con el log y hay dos arreglos ya descartados. La vía recomendada es **simplificar**: que esas
   vistas no pinten el «Cargando…» y pinten una sola vez cuando mpvd contesta (en local son milisegundos), con lo
   que la carrera desaparece y además se quita código.
4. **H49/G8**: las **491 cadenas** de mpvd que el extractor de G5 no veía porque no son `RpcError` (valores de
   diccionario, listas de presets, f-strings sueltas). Están medidas y repartidas por ficheros en el BACKLOG, por
   orden de lo que más se ve: `convert/presets.py` (32), `intro/service.py` (25), `subscriptions/chain.py` (20),
   `share/live.py` (18), `asr/models.py`, `jobs.py`, `ytdl/presets.py`, `power.py` (14 cada uno) y
   `share/service.py` (51). Hace falta además ampliar `tools/i18n_extract_py.py` a esas formas, o el test no las
   vigilará. **Primero** hay que ampliar el extractor; envolver a mano sin él deja el catálogo cojo sin avisar.
5. **La web (H50)**, diseñada en `docs/SITIO-WEB.md`. Ser la dejó fuera a propósito de esta tanda. Las capturas las
   puedo hacer yo; faltan dos decisiones suyas: qué se ve de fondo en ellas y si el repo se hace público.

Solo Ser puede: la línea de `sudoers` para `rtcwake` (despertar el equipo para una grabación), la cuenta de
OpenSubtitles y **comprobar el HDMI-CEC con una tele de verdad** (H61/M7). Las órdenes exactas están en
`NEEDS_HUMAN.md`.

## Registro por iteración
### Iteración 12 · 2026-10-02/03 · H57 · Programar que SUENE, y el repaso de ideas — hecho
- **Programar una franja ya no es solo grabar**: `mode` = grabar | ponerlo | las dos. No hay sistema nuevo: el
  programador de H40 ya tenía franja, despertador por RTC y los seguros de suspender/apagar; le faltaba el campo.
- **Si no hay reproductor abierto, mpvd abre uno.** Sin eso la función no sirve para lo que se pide: lo que da
  sentido a «a las 7:00 que suene la radio» es que el equipo esté suspendido, el despertador lo levante y no haya
  ninguna ventana. Si no llega a abrirse, la programación se marca fallida y dice por qué.
- **Una canción o una lista se envuelven como «canal»** (`media_channel`), así heredan despertador, franja, apagado
  y la lista de programaciones sin duplicar lo más delicado del programa.
- **docs/IDEAS.md**: repaso pedido por Ser de lo que tienen otros reproductores y aquí falta, lo que no tiene
  nadie, y mejoras de interfaz. Lo que yo haría primero: que se vea lo que pasa por detrás, avisar antes y no
  después, volumen parejo y temporizador de sueño general, listas guardadas, y «la charla de una hora en quince
  minutos» (transcripción + significado + corte, que ya están los tres).
- **Lección aplicada esta vez**: no se contesta hasta que `check.sh` termina.

### Iteración 11 · 2026-10-02 · H55 y H56 · Tercera prueba de Ser — hecho
- **Error de método, y es el que más conviene recordar**: di por buena la tanda H55 y contesté a Ser **antes de que
  terminara su `check.sh`**. Terminó con **once fallos**. Lo que Ser encontró luego era exactamente eso. Una tanda
  no está hecha hasta que la batería ha terminado.
- **H55**: los invitados de una sala privada entran pudiendo controlar (los mandos del navegador NO estaban rotos,
  se comprobó con un navegador real; lo que fallaba era que el permiso había que concederlo y estaba escondido);
  los dos enlaces se copian juntos y explicados; los tramos tienen icono propio, se reordenan y se unen en ese
  orden; y dos formatos nuevos, «Sin recodificar» (0,07 s por corte, sin pérdida) y AV1.
- **H56**: el invitado fantasma «Reproductor» ocupaba plaza y rompía salas con tope; guardar tramos funcionaba pero
  no se veía; la TV entraba pausada porque `pause` es global en mpv; y seis tests encodaban el contrato viejo.
- **Aviso**: hay tres tests sensibles a la carga que fallan en la pasada completa y pasan aislados (`test_books`,
  `test_mu_feeds`, `test_prefs`), y uno que fallaba **según la hora del día** (la guía de TV), ya arreglado.

### Iteración 10 · 2026-10-02 · H54 · Segunda prueba de Ser — hecho
- **«Si tira el vídeo para atrás se queda en el mismo minuto y tampoco tiene play/pause»: un solo fallo, y era la
  cara mala de C5.** La retransmisión empieza donde está el anfitrión y **solo contiene desde ahí**, así que al
  saltar a un minuto anterior ese minuto no existe y la corrección de deriva empujaba el vídeo al segundo 0 de la
  retransmisión una y otra vez: no estaba roto, estaba siendo recolocado sin parar. Ahora, si el salto cae fuera
  de lo que hay, **la retransmisión se rehace ahí** (con 3 s de gracia para que varios tirones cuenten como uno).
- **Y una de propina**: una retransmisión recién rehecha se anunciaba antes de que su lista existiera, y quien
  reenganchaba se llevaba un 404. Ya no se anuncia hasta que se puede pedir.
- **«El enlace no se puede reproducir en mpv o vlc»: no tiene arreglo por ahí, y conviene saber por qué.** El
  enlace de la sala lleva su contraseña en el FRAGMENTO (`#k=`), y un navegador no manda nunca el fragmento al
  servidor —que es justo lo que la mantiene fuera de los registros—, así que un reproductor no puede
  identificarse. El enlace que sí vale existía solo en la página del invitado; ahora el anfitrión lo tiene en
  *Compartir → Copiar el enlace para VLC o mpv*. Comprobado lanzando **un mpv de verdad** contra él.
- **«No veo la forma de elegir los que exportas»**: cada tramo entra marcado y Enter sobre su fila lo deja fuera;
  se guarda lo marcado, y *unir* solo se ofrece con más de uno elegido. Las acciones de cada tramo pasan a los
  botones de su fila.
- **Aviso repetido, que ya me ha mordido dos veces**: el estado se publica ANTES que las filas del menú, así que
  un test que espere por un contador puede ver filas viejas. Esperar siempre por la fila.

### Iteración 9 · 2026-10-02 · H51 y H52 · De la prueba de Ser — hecho
- **H51 · compartir (ADR-089).** De cuatro quejas, **tres eran el mismo fallo**: el minuto de espera no era
  lentitud sino un **enlace muerto**. Medido tres veces: `cloudflared` imprime la dirección a los 5,6-7,7 s y
  **no enruta hasta 65-69 s**; la dábamos por buena en el primer momento. Ahora la sala se abre al instante con su
  dirección local, el túnel va detrás (por eso tampoco salía el QR hasta pasados esos segundos: misma causa) y la
  dirección pública se sondea hasta que contesta; solo entonces se copia y se avisa. El enlace se copia **solo** al
  crear la sala y el QR deja de plantarse en pantalla. Internet pasa a estar **encendido por defecto**, y por eso
  `tools/vendor.sh` instala ya `cloudflared`: un ajuste encendido que no puede cumplirse es peor que no tenerlo.
- **H51 · «en el invitado no cambia la película»: dos fallos distintos.** La dirección del archivo era
  `/s/<sala>/file` —la misma cadena para TODAS las películas—, así que nadie podía saber que había otra cosa; y en
  la página `useRelay` se ponía a `true` y **no volvía nunca**, de modo que tras una película que el navegador no
  abre, la siguiente que sí abría se quedaba sin dirección y el invitado veía la anterior para siempre.
- **H51 · «solo desde el navegador, no desde VLC o mpv»**: *Abrir en mi reproductor* solo existía para un archivo
  del anfitrión. Ahora vale también para la TV y los vídeos de internet, y para eso hubo que reescribir la lista
  HLS con la credencial **en cada trozo**: los nombra en relativo y un reproductor se habría llevado un 401.
- **H52 · la barra y la línea de tiempo (ADR-090).** Botones de **tramos** (con el número de insignia), **bucle** y
  **nota**; lo elegido se pinta en la línea de tiempo y las notas salen como rombos. Los tramos se guardan sueltos
  o **unidos en uno** (una sola pasada de ffmpeg, `trim`+`concat`), en vídeo o solo audio. La barra se agrupa por
  significado y «solo audio» sale de ella. Diseño razonado en `docs/INTERFAZ.md`.
- **Lo que condicionó el diseño, comprobado y no supuesto**: la barra de uosc **no se puede rehacer en caliente**
  (`Controls:init_options()` solo corre en `init()`), así que un «modo edición» exigiría parchear uosc; y
  `chapter-list` —lo que uosc dibuja— **ya lo escribía mu-subs**, así que ahora tiene un dueño único, `mu-marks`.
- **Aviso para quien siga**: no edites el código mientras corre `tools/check.sh`. Lo hice y la pasada se llenó de
  fallos que no existían; los tests cargan los scripts del disco.

### Iteración 8 · 2026-10-02 · H44/C6 · Entrar en la sala desde el reproductor — hecho
- **El modo invitado vive en mpvd** (`mpvd/share/guest.py`, nuevo), no en Lua: entrar, mantener el canal de eventos
  abierto y corregir la deriva son red y reloj, y el hilo de Lua no puede bloquearse ni habla HTTP. El script Lua
  pide el enlace, enseña en qué sala estás y sale. mpvd manda `seek`, `pause` y `speed` al mpv que pidió entrar, por
  el IPC que ya existía.
- **El invitado reproduce el fichero ORIGINAL** (`/s/<sala>/file?k=…`), no el relay del navegador: es la razón de
  ser de esto (calidad original, todos los códecs, saltos instantáneos, cero CPU del anfitrión). El relay solo se
  usa cuando no hay otra cosa (un directo).
- **La corrección de deriva es la misma cuenta que la página**, portada constante a constante de `www/sync.js`, con
  un test que **ejecuta las dos con node y las compara**. Dos implementaciones separándose serían dos experiencias
  distintas en la misma sala, y es el tipo de diferencia que nadie ve hasta que alguien dice «yo lo tengo cuatro
  segundos por delante».
- **Dos cosas que hicieron falta y no estaban previstas**: (1) las rutas `media/` y `subs/` aceptan ahora la
  credencial en la query (`?k=`) igual que `file`, porque **un reproductor de verdad no manda cookies** y sin eso un
  directo no se puede seguir; (2) el aviso «esa sala es tuya» bloqueaba a **cualquier** reproductor del mismo
  equipo, cuando lo que hay que impedir es que el anfitrión se siga a sí mismo. Se afinó a la sesión del anfitrión:
  dos ventanas en el mismo PC es justo como se prueba esto, y como lo usará quien tenga dos pantallas.
- **El enlace entra por los tres sitios por los que puede llegar**: el lanzador (`bin/mpv-uos "<enlace>"` abre ya
  dentro y **no** se lo pasa a mpv, que se lo daría a yt-dlp para descargar una página web; igual en `mpv-uos.ps1`,
  con el test de paridad ampliado), la puerta única de H42 (pegarlo ofrece *Entrar en esa sala* en vez de
  reproducir o descargar) y *Compartir → Entrar en una sala de otro…*. `mu/clip.lua` gana `read()`/`first_line()`.
- Tests: `tests/test_share_guest.py` (el enlace, la paridad con sync.js bajo node, dos mpv de verdad entrando y
  siguiéndose, el lanzador y el menú de punta a punta).

### Iteración 7 · 2026-10-02 · H42-H46 · La interfaz y la sala — hecho (falta H44/C6)
- **H42 · Una sola puerta** (ADR-077). `mu-ytdl` vista `gate` (`ctrl+o`, y primera fila de *Abrir o descargar*):
  clasifica lo pegado —varios enlaces, uno, uno sin esquema, una ruta, una carpeta, un `.txt`/`.list`/`.urls`/`.csv`
  o el portapapeles entero— y luego hace **una** pregunta. A mpvd solo se le pregunta cuántos elementos trae si la
  URL **huele a lista** (`list=`, `/playlist`, `/@algo`, `/channel/`…): un vídeo suelto no necesita red para decir
  «1 elemento», y la respuesta se reutiliza en la pantalla de descarga. La extensión y el tamaño se miran **antes**
  de leer el archivo (el código anterior le pasaba cualquier ruta a `read_links`, que leía el fichero entero: 4 GB
  en memoria con una película). `mu/clip.lua` unifica el portapapeles, que estaba escrito tres veces.
- **ADR-078 (fallo real encontrado de paso).** `mu-menu` borraba su pila al ver el `nil` que uosc publica mientras
  **sustituye** un menú por otro, así que al volver de un módulo el siguiente `⌫` cerraba todo en vez de subir un
  nivel. Se veía como un test intermitente (`test_nav`), no como un fallo.
- **H43 · Grabar** (ADR-079). Fila «Formato» que se recuerda, y que dice de antemano si los códecs caben en MP4.
  «Grabar solo el audio» deja de ser una fila aparte: era la misma elección contada dos veces. «Programar una
  grabación…» al primer nivel de TV y radio y de Grabar. **Ojo**: de las dos condiciones que el análisis señalaba
  para la radio, solo una lo impedía; la otra es el filtro de la guía de TV, que la radio no tiene.
- **ADR-080 (fallo real).** uosc deriva el id de un submenú de su **título**: dos grabaciones programadas del mismo
  canal compartían id y uosc reventaba al pintar el menú. Se arregla con el `id` explícito que admite su API, en las
  tres listas donde el título sale de datos.
- **H45 · El resumen en los vídeos de internet** (ADR-081). Se piden los subtítulos de la web al pulsar (5,41 s
  medidos frente a ~8 min de Whisper), solo en idiomas nativos (las traducciones automáticas de YouTube dan 429) y
  con caché. **No** se añade `write-auto-subs` a `mpv.conf`: con `--sub-langs all` el `-J` pasa a 14 MB por vídeo.
  «Resumen e índice» pasa a la raíz y al panel de subtítulos, y sale de «Herramientas».
- **H44 · La sala** (ADR-082). C1 (el handler lee por trozos; 300 MB servidos sin que crezca el RSS), C2/C3 (el
  fichero original, el `.m3u` y la credencial del invitado en la query, porque mpv y VLC no mandan la cookie),
  C4 (códecs + contenedor + **dónde está el `moov`**: «es un MP4» no vale), C5 (el relay empieza donde va el
  anfitrión: era la causa de los ~18 min) y C7 (WebTorrent, no-objetivo, con los números). **Falta C6**.
- **H46 · Un solo menú** (ADR-083). Fuera el árbol de comentarios de `input.conf` (119 entradas, 40 en el primer
  nivel, cuatro cosas repetidas) y la tecla que lo abría. Antes de borrarlo: dos cosas vivían **solo** allí
  (repetir la lista, orden aleatorio) y la **paleta leía de ahí sus títulos**, así que los 67 que faltaban pasaron a
  `CURATED`, con la tecla leída del reproductor. Tres tests impiden que vuelva.
- Probar a mano: los comandos están arriba, en el resumen de la iteración 7.

### Iteración 6 · 2026-10-01 · H37-H40 · Descargas, TV, resumen y energía — hecho
- **H37 · Descargar por una sola puerta** (ADR-072). Antes había que saber de antemano si lo tuyo era «varias URL» o «una
  lista o canal»; ahora hay una fila, *Descargar…*, y pegas lo que sea: un enlace, veinte, una lista, un canal o un
  `.txt`. Todo acaba en la **misma lista con casillas**: la primera fila fija el formato de todos, cada fila puede llevar
  el suyo (Tab sobre la fila) y hay casilla de SRT global y por fila. Se baja solo lo marcado, y las filas que comparten
  formato van en un solo lote. **Lo de Instagram/TikTok, comprobado contra tu yt-dlp**: los *guardados* de Instagram y
  los favoritos de TikTok **no tienen extractor** (no es que falle: no existe), así que el menú lo dice con la
  alternativa —pegar los enlaces— en vez de fallar; el perfil de Instagram se ofrece avisando de que yt-dlp lo marca
  roto, y perfiles y colecciones de TikTok sí funcionan.
- **H39 · TV, radio y lo que no quieres ver** (ADR-073). **Las radios españolas**: el salto a otro espejo solo se
  disparaba con un error de carga, y las que no suenan no dan error (se quedan conectando). Ahora, si a los 8 s el reloj
  no ha avanzado, se pasa sola a la siguiente copia. La lista además **dice lo que dijo la comprobación** con palabras
  («✕ no responde», «prohibido: suele ser geobloqueo») y, en un canal repetido, cuántas de sus copias respondieron.
  **SponsorBlock al VER** (no solo al descargar): los tramos marcados por la gente se saltan solos, y se piden por un
  prefijo de 4 caracteres del hash del id del vídeo, así que **no se dice a nadie qué estás viendo**. El icono de saltar
  tiene tres estados que se ven: buscando, saltar y no hay.
- **H40 · Despertar para grabar y apagar al terminar** (ADR-074). En *Grabaciones programadas*: «despertar el equipo
  5 min antes» y «al terminar: nada / suspender / apagar». Mientras graba, el equipo no se duerme. Antes de suspender o
  apagar se comprueban tres cosas (que no estés usando el reproductor, que no haya otra grabación a menos de 15 min, que
  no quede nada descargando o transcribiendo) y sale un aviso de 60 s con **Cancelar**. Suspender y apagar ya funcionan
  sin contraseña; el **despertador** necesita una orden con `sudo` una sola vez, y está en NEEDS_HUMAN.md con cómo
  comprobarla y cómo quitarla. Sin ella no se pierde ninguna grabación: se programa igual y avisa.
- **H38 · Índice del vídeo y resumen** (ADR-075 y ADR-076). `alt+I`: **índice** con secciones, su título y sus frases
  clave, cada línea a su minuto, **al instante** y sin IA generativa (los títulos son frases del propio diálogo). Al
  final del índice, **resumen en prosa** escrito por un modelo que corre en tu equipo: corto (unos 40 s) o largo (sobre
  un minuto), con el tiempo dicho antes de empezar. El modelo se eligió **midiendo** tres candidatos con 15 min de una
  charla real (docs/BENCHMARKS_LLM.md): gana gemma-3-1b (806 MB); qwen2.5-3b queda como «escribe mejor, el doble de
  tiempo» y qwen2.5-1.5b se descartó por repetirse. Los minutos **no los pone el modelo**: salen del subtítulo y lo que
  invente se quita antes de que lo veas. Nunca se lanza una transcripción para resumir: si no hay subtítulos, se dice.

Cómo probarlo a mano: `alt+y` → *Descargar…* y pega dos enlaces de YouTube (uno en audio Opus, el otro en 480p con SRT:
Tab sobre la primera fila). `alt+t` → una radio con «+N fuentes» (si la primera no suena, verás «Probando otra fuente»).
En un vídeo de YouTube con patrocinios, `alt+j` para ver los tramos y `alt+k` para saltarlos. Con una peli con
subtítulos, `alt+I` → el índice y, al final, *Resumen en prosa (corto)*.

### Iteración 6 · 2026-10-01 · H35 · Arreglos de uso y la barra como mando — hecho
Los nueve apuntes de tu prueba real, por orden de lo que molestaba:
- **El botón ● de grabar**: el primer clic pregunta (vídeo o audio), el segundo **para** la grabación sin preguntar nada.
- **El QR de la sala se puede quitar**: el temporizador de 120 s ya no se rearma cada vez que cambia algo de la sala (con
  una sala viva no se iba nunca), la fila del menú alterna de verdad («Ocultar el código QR») y `alt+Q` lo pone y lo saca.
- **El invitado veía la pantalla en blanco** mientras se preparaba la retransmisión y tú no podías saber si iba: ahora el
  anfitrión ve «Preparando la retransmisión… 0:45 listos de 2:10» en su menú, y la página del invitado lo dice también.
- **La barra de abajo como mando**: botón de subtítulos (abre el panel, no la lista de pistas de uosc) y botón de
  «solo audio» (quitar el vídeo), que antes solo estaba en `alt+a`.
- Benchmarks: recuperada la sección de los trozos de 28,5 s que se perdió al regenerar el fichero, y la tabla por tier
  ahora se genera desde el código, así que no puede volver a desviarse de lo que hace el programa.
- `tools/check.sh` pasa ahora **siempre** LuaJIT además de luacheck: un `//` (división entera, que LuaJIT no tiene) colaba
  el lint y rompía el script al cargarlo en mpv. Pasó de verdad, y así no vuelve a pasar.

### Iteración 6 · 2026-10-01 · H36 · Subtítulos: sencillos, buenos y sin esperas en vivo — hecho
Lo que dijiste: «en vivo nada de subtítulos», «el traductor debe ser de la máxima calidad», «subtítulos más sencillo e
intuitivo» y «al cerrar, si hay algo por detrás, pregúntame».

- **Fuera los subtítulos en vivo** (ADR-070). Antes se transcribía persiguiendo la reproducción, y eso obligaba a elegir
  el modelo por velocidad: en este portátil, `base`, que escribe con faltas y casi sin puntuación (y eso es justo lo que
  estropea después la traducción). Ahora hay un solo modo: se prepara el archivo entero desde el segundo 0 con el mejor
  modelo que aguante el equipo. En este portátil, `small-q8_0`, que va a 0,45 (más del doble de rápido que el vídeo).
- **La espera se dice con números, y son medidos.** Cada tarea publica el ritmo de los últimos trozos, lo que queda a ese
  ritmo y lo que tardaría el modelo rápido. En pantalla: «listos en 7 min · va más rápido que el vídeo, no te alcanzará»
  o, con un modelo lento, «listos en 1 h 10 min · con small-q8_0, 6 min». Si el portátil se carga, el aviso sube con él.
  Por qué no son los modelos grandes por defecto: medidos aquí, `medium-q5_0` va a 4,03 y `large-v3-turbo-q5_0` a 5,22,
  o sea 4 y 5 veces más lento que el propio vídeo (una película de 2 h: 8 h y 10 h 30 de CPU), y en la muestra aciertan
  lo mismo que `small-q8_0`. Están a un clic, con el tiempo calculado delante.
- **Al cerrar, se pregunta** (C8). `q` ya no es «cerrar y a ver qué pasa»: mpvd dice qué sigue trabajando y, si son los
  subtítulos de ESTE archivo, se pregunta siempre («Seguir en segundo plano» / «Dejarlo»); descargas y conversiones
  admiten «no volver a preguntar»; una grabación programada no se pregunta ni se para nunca. Si cierras la ventana y
  quedaba algo, sale un aviso de escritorio con botón **Parar**; al volver a abrir, un recordatorio. Y el menú principal
  tiene siempre una línea que dice qué se está haciendo por detrás.
- **Buscar subtítulos, guiado** (C4). Antes, sin clave de OpenSubtitles salía «falta la Api-Key» y ahí se acababa. Ahora
  son dos pasos: *Abrir la página de la clave* (es gratis) y *Pegar la clave del portapapeles*; la clave la lee mpvd por
  su propia conexión, así que no pasa por la pantalla ni por ningún log. Con cuenta, el menú dice el cupo que te queda.
- **Traducir, a la máxima calidad** (C6). Añadidos los modelos OPUS-MT de francés, así que es/en/fr se traducen con
  OPUS-MT de punta a punta (es↔fr por inglés: no existe modelo directo). El motor por defecto pasa a ser «máxima
  calidad»: OPUS-MT donde llegue y Argos para los demás idiomas. Comprobado traduciendo de verdad en los dos sentidos.
- **Una sola lista de dónde salen los subtítulos** (C5, ADR-071): `subs.find` junta la web del vídeo y OpenSubtitles y los
  ordena por fiabilidad (del propio vídeo · reconoce este archivo exacto · coincide el título · automáticos), y dice qué
  proveedores hay y **por qué** falta alguno. De los dos que íbamos a añadir: **Podnapisi está muerto** (su dominio ya no
  existe) y **Subdl necesita una clave gratuita** (está en NEEDS_HUMAN.md, 2 minutos). El API antiguo de opensubtitles.org
  tampoco sirve: busca sin clave, pero las descargas anónimas devuelven 102 bytes con un anuncio en vez del subtítulo.
- **El panel, en tres bloques** (B3): lo que ya hay (las pistas, con el tamaño del texto y el retraso siempre a mano) ·
  buscar en internet · y al final crear con IA, que es lo lento. Se llama «Subtítulos del vídeo», porque ya no es solo IA.

Cómo probarlo a mano: `bin/mpv-uos <una peli tuya>` → `alt+i`. Arriba están sus pistas (Enter las pone, «Sin subtítulos»
las quita) y el tamaño y el retraso. *Buscar subtítulos en internet* enseña todo lo que hay junto, con su fiabilidad y,
abajo, por qué falta algún proveedor. *Crear los subtítulos con IA* arranca la transcripción y a los pocos trozos dice
cuánto falta; cierra con `q` mientras va y te preguntará qué hacer (y si cierras la ventana, mira el aviso del escritorio).

### Iteración 5 · 2026-10-01 · H25 · Túnel de internet para las salas — hecho (ADR-068)
Era lo único que quedaba `[ ]` en todo el BACKLOG, y ya tenía tu permiso. Ahora una sala de «ver juntos» se puede abrir
desde fuera de casa:
- `mpvd/share/tunnel.py`: túnel rápido de Cloudflare (`cloudflared tunnel --url …`), sin cuenta ni configuración. Se
  arranca al abrir la sala y **el proceso muere al cerrarla**, así que nada del reproductor es alcanzable desde internet
  ni un segundo más que la sala; la dirección es nueva cada vez y para entrar sigue haciendo falta el enlace con su token.
- **Apagado por defecto**: *Compartir → Que se pueda entrar desde internet* (se recuerda en mu-prefs). Si está apagado,
  todo sigue exactamente como antes, solo en tu red.
- El binario **no** viene de serie: `MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh` (ya ejecutado aquí), fijado y verificado por
  SHA-256 en `vendor.lock`. Si falta o el túnel falla, la sala se abre igual en la red local y el menú dice por qué.
- Con túnel no hace falta tocar el cortafuegos ni el router: desaparece el aviso de `ufw` de las salas.
- Probado con un `cloudflared` falso (9 tests en `tests/test_share_tunnel.py`: la dirección se lee de su salida, el enlace
  de la sala la usa, el proceso muere con la sala, y los errores se cuentan con sus propias palabras) **y con un túnel
  real**: la dirección pública devolvió lo que servía el servidor local y el proceso murió al cerrar. Ese test real está
  en la suite pero apagado (`MPV_UOS_TEST_TUNNEL=1`), para que `check.sh` no publique nada en cada pasada.
- Probar a mano:
  ```bash
  mpv-uos tests/fixtures/media/video30.mkv      # alt+W → «Que se pueda entrar desde internet» → crear la sala
  MPV_UOS_TEST_TUNNEL=1 uv run pytest tests/test_share_tunnel.py -q -m network   # el túnel de verdad
  ```

### Iteración 5 · 2026-10-01 · H34 · Cribado de los 4 fallos de check.sh — hecho
La última pasada completa dio 706 pasan, 4 fallan. Cribados uno a uno (con tu `llama-server` ocupando ~3,5 de los 4
núcleos, que es justo lo que hace inestables a los que miden tiempos):
- `test_asr_service` → **era mío y está arreglado**: el SRT de una tarea de subtítulos IA lleva ahora la pista de audio en
  el nombre (`base.en.a1.srt`), que es el arreglo para que el doblaje no reutilice los subtítulos de la VO; la
  expectativa del test seguía con el nombre antiguo. Pasa.
- `test_mu_iptv_live` → **era la carga**: pasa aislado (va de `watch_later` y saltos en un HLS en directo, nada que
  tocara H34).
- `test_mu_subs` → **es la carga, con prueba**: el estado publicado trae `rtf: 2.112`, o sea que whisper va a 2,1× el
  tiempo real cuando con la máquina libre va a ~0,3×; de ahí el timeout de 120 s.
- `test_mpris` → **era el test, no el reproductor, y está arreglado**. mpvd emite `Seeked` cuando debe y con el valor
  correcto (log del daemon: `pos=0` al cargar, `pos=10000000` y `pos=20000000` tras cada salto). El ayudante `saw()` del
  test llamaba a `recv_until_filtered`, que **extrae y devuelve** el mensaje por el que esperaba, e ignoraba ese valor
  devuelto: así tiraba a la basura justo la señal que buscaba, sin comprobarla. Con `PropertiesChanged` colaba porque
  llegan varias seguidas y alguna quedaba en la cola; con `Seeked`, que es una por salto, nunca. Comprobado que el test
  arreglado falla si se quita la emisión de la señal en mpvd.

### Iteración 5 · 2026-10-01 · H34 · Revisión de calidad — hecho
Siete revisiones de código por áreas (TV, descargas/convertir, subtítulos, biblioteca/suscripciones/notas, audio,
compartir/mando, núcleo/UI) y **42 fallos reales confirmados leyendo el código y corregidos, cada uno con su test**. Diez
commits, «H34 (1)»…«H34 (10)». Los más graves, por si quieres comprobarlos a mano:

1. **Notas** (`H34 (1)`): el `.md` de «Mis notas» se anuncia como editable en Obsidian, pero cualquier texto que
   escribieras desaparecía al añadir, editar o borrar una nota. Además, un `.md` ajeno dejado en la carpeta de notas se
   reescribía y se renombraba, «Exportar a una carpeta» sobrescribía sin avisar un fichero tuyo con el mismo nombre, y un
   título en japonés o con emoji pasaba de los 255 bytes y la nota se perdía con un error técnico.
   ```bash
   mpv-uos tests/fixtures/media/video30.mkv    # alt+b una nota; abre el .md, escribe un párrafo, otra nota: sigue ahí
   ```
2. **Subtítulos IA** (`H34 (1)`): la frase de cada frontera de bloque (28,5 s) salía dos veces, y un cue corto que cayera
   en la pre-rodadura se escribía con el fin antes del inicio (SRT corrupto). Con VO + doblaje se servían los subtítulos
   de la otra pista, también desde la caché. Y todas las traducciones de subtítulos de internet se escribían en el mismo
   archivo, así que se pisaban entre vídeos.
3. **Descargas** (`H34 (2)`, `H34 (10)`): cancelar una que aún no había empezado la dejaba «en cola» para siempre y se
   relanzaba al reiniciar; un lote de más de 200 enlaces perdía de la lista los primeros; parar mpvd marcaba la cola como
   «cancelada» en vez de reanudarla (contra el criterio de H19); «Instalar yt-dlp» nunca funcionaba; y como cada descarga
   ocupaba un worker de la cola de trabajos, tres bastaban para que los subtítulos en vivo o una traducción no arrancaran.
4. **Grabar** (`H34 (3)`): «Grabar desde ahora» recortaba el fichero equivocado si mpv pasaba al episodio siguiente;
   había dos dueños de `stream-record` (el menú de TV y el botón ●) con carpetas distintas, así que el punto rojo se
   quedaba pegado; y cualquier grabación local de más de 10 minutos fallaba por heredar el tope de los clips de estudio.
5. **TV** (`H34 (4)`, `H34 (8)`): un fallo de red al bajar la guía la dejaba en bucle infinito de descargas de 120 s; una
   excepción en el bucle del programador mataba todas las grabaciones programadas sin avisar; y «Actualizar listas»
   congelaba mpvd (el índice se reconstruía en el bucle de eventos, una vez por lista, con ~12.000 canales).
6. **Salas y mando** (`H34 (4)`, `H34 (7)`): al cerrar una sala podían quedar ffmpeg transcodificando la película entera
   y recrear la carpeta borrada; `alt+z` bloqueaba el daemon hasta 6 s con `systemctl`; el 500 del servidor devolvía el
   texto de la excepción (con rutas) a quien estuviera en la red local; 20 erratas acumuladas inutilizaban el enlace de la
   sala; y en sala privada las plazas no se liberaban nunca.
7. **Audio** (`H34 (5)`, `H34 (9)`): en un álbum con algún archivo que falta, Enter sonaba otra pista; el temporizador de
   apagado dejaba el volumen bajado si retrocedías durante el fundido; y mover en la cola con el buscador activo movía otra
   entrada.
8. **Biblioteca y suscripciones** (`H34 (4)`, `H34 (6)`): quitar una carpeta borraba también las filas de una subcarpeta
   que sigue en la biblioteca; renombrar una suscripción dejaba huérfano todo lo ya movido; y las carátulas reintentaban
   ffmpeg en cada escaneo cuando fallaban.

Además, dos guardianes nuevos contra este tipo de deriva:
- `tests/test_integracion_lua_rpc.py`: todo `rpc.call` de los scripts Lua existe en mpvd y sus parámetros cuadran (205
  llamadas comprobadas contra los 244 métodos registrados). Habría cazado el fallo de «Instalar yt-dlp».
- `tests/test_flujos_e2e.py`: saltar de un módulo a otro con su tecla sin pasar por «Atrás» (que la pila de navegación no
  se acumule) y que nota + preferencia + posición sigan ahí tras reiniciar mpv contra el mismo daemon.
- ADR-069: por qué las descargas esperan en su propia cola y por qué NO se pone un semáforo de trabajos «heavy».

### Iteración 4 · 2026-09-30 · H28 · Windows (punto 4) — hecho (rama de subagente de la iteración 3, fusionada)
- `bin/mpv-uos.ps1` (+ `bin/mpv-uos.cmd`): mismas opciones que `bin/mpv-uos`, pipe `\\.\pipe\mpv-uos-<pid>-<azar>` por instancia,
  `-DryRun` y `-Gui`. `tools/install.ps1`: sin administrador, `uv sync`, binarios de Windows de `vendor.lock` verificados con
  SHA-256 (yt-dlp.exe, ziggy, deno, whisper.cpp CPU con `-Whisper`), menú Inicio, `mpv-uos://` en HKCU, `-DryRun`, `-Uninstall`.
- mpvd: bloqueo de arranque con `msvcrt.locking` en Windows, `whisper-cli.exe`, el actualizador de yt-dlp baja `yt-dlp.exe`.
- ADR-066; docs/PLATAFORMAS.md (tabla y «Instalar en Windows»), README y NEEDS_HUMAN.md (prueba en un Windows real).
- Tests: `tests/test_windows_scripts.py` (30: estáticos + pwsh 7 portátil en `.cache/pwsh`: parser real, `-DryRun` igual que
  el lanzador Bash, descargas contra un servidor local, instalar/desinstalar en carpeta temporal).
- Probar a mano (en Linux, sin Windows):
  ```bash
  uv run pytest tests/test_windows_scripts.py -q
  .cache/pwsh/pwsh -NoProfile -File bin/mpv-uos.ps1 -DryRun tests/fixtures/media/chapters.mkv
  .cache/pwsh/pwsh -NoProfile -File tools/install.ps1 -DryRun -NoSync
  ```

### Iteración 3 · 2026-09-30 · H32 · Biblioteca musical (punto 1) — hecho (subagente, fusionado)
- mpvd `music/` (`music.*`: carpetas, escaneo incremental con ffprobe, artistas/álbumes/géneros/años, búsqueda sin
  acentos, carátulas, ReplayGain 2.0 medido en segundo plano, listas M3U8 e inteligentes, historial local) (ADR-064).
- mu-music (`alt+M`, también en *Abrir → Música*): cola con «Reproducir a continuación», sin cortes, fundido por
  `volume-gain`, volumen igualado (etiquetas o `replaygain-fallback`), salida exclusiva; perfiles de auriculares en mu-av.
- Arreglo: mu-av ya no publica la vista nueva con las filas de la anterior (carrera en test_mu_av con carga).
- Tests: test_music_service.py, test_mu_music.py, test_nav (mu_music), test_mu_av.
- Probar a mano:
  ```bash
  mkdir -p tmp/musica/Artista/Disco && for n in 1 2 3; do ffmpeg -loglevel error -y -f lavfi -i sine=f=$((300*n)):d=20 \
    -metadata artist=Artista -metadata album=Disco -metadata title="Pista $n" -metadata track=$n tmp/musica/Artista/Disco/0$n.mp3; done
  MPV_UOS_MUSIC_DIR=$PWD/tmp/musica bin/mpv-uos   # alt+M → Artistas → Artista → Disco; Tab → Reproducir a continuación
  ```
  En *Ajustes*: Sin cortes, Fundido 3 s, Volumen igualado «por álbum»; `alt+v` → Ecualizador → Auriculares cerrados.

### Iteración 3 · 2026-09-30 · H32 · Letras, carátulas, audiolibros y podcasts (punto 2) — hecho (subagente, fusionado)
- mpvd `lyrics.py` (`.lrc`/etiqueta/LRCLIB opcional → SRT en caché), `books.py` (detección, posición pista+tiempo,
  velocidad, marcadores) y `songid.py` (AcoustID con clave propia, desactivado) (ADR-065). Carátulas: mpv solo.
- mu-books (`alt+A`, `alt+J`/`alt+L` ±30 s, temporizador de apagado) y mu-lyrics (`alt+K`).
- Tests: test_lyrics.py, test_books.py, test_nav (mu_books, mu_lyrics).
- Probar a mano:
  ```bash
  printf ';FFMETADATA1\ntitle=Libro\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=60000\ntitle=Uno\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=60000\nEND=120000\ntitle=Dos\n' > tmp/libro.ffmeta
  ffmpeg -f lavfi -i sine=d=120 -i tmp/libro.ffmeta -map_metadata 1 -map_chapters 1 -c:a aac tmp/libro.m4b
  bin/mpv-uos tmp/libro.m4b   # alt+A → Velocidad ×1.5, Añadir marcador…, Temporizador → Al terminar el capítulo
  bin/mpv-uos <canción con su .lrc al lado>   # la letra sale como subtítulo; alt+K → Enter en una línea
  ```
- Pendiente: «al terminar el capítulo» pausa ~0,3 s antes del cambio; sin ventana en audios sin carátula.

### Iteración 3 · 2026-09-30 · H28 · AppImage y .app de macOS — hecho
- `tools/build_appimage.sh` → `dist/MPV-UOS-x86_64.AppImage` (41 MB: app + CPython 3.12 de uv + yt-dlp; mpv del
  sistema), yt-dlp actualizable en `<datos>/bin` (`MPV_UOS_VENDOR_BIN`) (ADR-067). `tools/build_macos_app.sh` → `.app`
  mínimo (sin probar en un Mac). Tests: test_appimage.py (contenido, Python reubicado, arranque sin ventana con mpvd),
  test_macos_app.py. ARM64/macOS: NEEDS_HUMAN.
- Probar a mano:
  ```bash
  tools/build_appimage.sh && dist/MPV-UOS-x86_64.AppImage tests/fixtures/media/video30.mkv
  ```

### Iteración 3 · 2026-09-30 · H25 · Sala pública, chat y «Emitir en directo» — hecho (subagente, fusionado)
- Sala pública solo ver (LAN, `&v=1`, hasta 20 espectadores anónimos), chat y reacciones en salas privadas (límites,
  texto nunca como HTML), `live.*` por RTMP/RTMPS con ffmpeg (clave 0600 leída del portapapeles por mpvd, 720p30,
  VA-API con reintento por CPU) (ADR-061). Tests: test_share_live (emisiones reales a un receptor local), test_share_*.
- Probar a mano:
  ```bash
  ffmpeg -listen 1 -timeout 120 -f flv -i rtmp://127.0.0.1:1935/live/prueba-1234 -c copy tmp/emision.flv &
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+W → Emitir en directo… → Otro servidor → rtmp://127.0.0.1:1935/live
                                                 # copia «prueba-1234» → Pegar la clave → Emitir lo que estoy viendo → Parar
  ffprobe -v error -show_entries stream=codec_name -of csv tmp/emision.flv   # h264 y aac
  ```
- Túnel a internet: [~] (NEEDS_HUMAN.md). Falta reconexión automática de la emisión.

### Iteración 3 · 2026-09-30 · H28 · Transporte de mpvd para Windows — hecho (sin probar en Windows)
- `mpvd/transport.py`: socket Unix o named pipe `\\.\pipe\mpv-uos-mpvd-<usuario>` (lazo Proactor) para el servidor
  de mpvd, el cliente y la conexión IPC con mpv; `Settings.endpoint`. Test con un lazo simulado sobre sockets Unix.

### Iteración 3 · 2026-09-30 · H27 · Enviar a la tele (DLNA) — hecho
- mpvd `cast/` (`cast.discover/play/control/status/stop`): SSDP + UPnP AVTransport/RenderingControl; el archivo tal
  cual con rangos de bytes (puerto 8792, token por elemento) o relé MPEG-TS de ffmpeg; URL directa para vídeos web con
  MP4 progresivo (ADR-063). mu-cast (`alt+E`, *Herramientas*): teles de la red, enviar desde el minuto actual (aquí se
  pausa), pausar, ±30 s, volumen, *Seguir viendo aquí*, parar.
- Tests: test_cast.py (descripción, SSDP, SOAP, DIDL, relé; tele falsa en loopback que descarga el medio; mu-cast
  headless). Sin tele real probada; Chromecast pendiente.
- Probar a mano (tele encendida en la misma wifi; antes `sudo ufw allow from 192.168.1.0/24 to any port 8792 proto tcp`):
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+E → tu tele → Seguir viendo aquí
  .venv/bin/python -m mpvd call cast.discover
  ```

### Iteración 3 · 2026-09-30 · H23 · Menú «Suscripciones» y panel web de descargas — hecho (subagentes, fusionado)
- mu-feeds (`alt+Y`, *Descargas y conversión › Suscripciones*): añadir por URL con detección, reglas, cadena tras
  descargar, pendientes/descargados, pausar, borrar, ajustes (franja, límite, red medida) (ADR-059).
- Panel `/downloads` en el servidor del mando: tareas en vivo, selección múltiple, pegar/arrastrar enlaces, disco,
  aviso al terminar; `mpv-uos://download?url=…` → `python -m mpvd link` (cola sin abrir el reproductor); marcadores
  (ADR-060). Tests: test_mu_feeds, test_downloads_panel, test_launcher.
- Probar a mano:
  ```bash
  bin/mpv-uos   # alt+Y → Añadir suscripción… → https://www.youtube.com/@BlenderOfficial → Suscribirse
  bin/mpv-uos   # alt+Z → Panel de descargas en el navegador → pega un enlace → Descargar
  .venv/bin/python -m mpvd link 'mpv-uos://download?url=https%3A%2F%2Farchive.org%2Fdetails%2FCountdow1960&preset=audio_original'
  ```
- Falta: volver a «usar la cadena general» en una suscripción con cadena propia; notificaciones con la página cerrada
  (sin HTTPS no hay push).

### Iteración 3 · 2026-09-30 · H27 · «¿Qué me he perdido?» — hecho
- mpvd `recap.py` (`recap.summarize`): frases del propio diálogo del tramo (SRT/VTT/ASS externo, pista de texto
  incrustada extraída a la caché, o transcripción IA ya hecha); embeddings de `semantic` si están, si no palabras que
  se repiten; MMR para no repetir; 3–7 frases con su minuto (ADR-062).
- mu-recap (`alt+R`, *Herramientas*): sigue foco/minimizado; al volver tras ≥ 60 s avisa; Enter salta a la frase.
- Tests: test_recap.py (selección, ventana, fuentes por RPC con pista incrustada real, mu-recap headless con ausencia
  simulada y salto, sin subtítulos).
- Probar a mano:
  ```bash
  bin/mpv-uos --sub-file=<un .srt> <su vídeo>   # minimiza 2 min, vuelve: «¿Te has perdido algo? alt+R» → alt+R → Enter
  ```

### Iteración 3 · 2026-09-30 · H27 · Mini reproductor, modo salón y modo sencillo — hecho
- mu-modes: mini (`alt+F`: ventana ~30 % del ancho, sin bordes, encima), salón (pantalla completa, uosc 1,8×,
  subtítulos y OSD grandes; mando de consola), sencillo (menú principal de 4 categorías + «Menú completo», barra
  mínima). Cada modo devuelve lo que cambió; salón y sencillo se recuerdan. En *Preferencias* (ADR-058).
- mpvd `gamepad.py`: API de joystick de Linux (`/dev/input/js*`) en un hilo solo con el modo salón; A pausa, cruceta
  ←/→ 10 s, ↑/↓ volumen, X subtítulos, Start menú, B cierra, LB/RB anterior/siguiente.
- Tests: test_mu_modes.py (aplicar/restaurar, recordar, menú sencillo, gamepad con una FIFO).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+F mini · alt+m → Preferencias → Modo salón (con un mando Xbox)
  bin/mpv-uos                                    # alt+m → Preferencias → Modo sencillo → alt+m muestra 4 categorías
  ```

### Iteración 3 · 2026-09-30 · H30 · TV: pistas del canal y «Buscar en esta lista» — hecho (subagente, fusionado)
- mpvd `iptv/tracks.py` + `iptv.tracks` (nombres legibles desde el master HLS y el track-list; CC/VO/AD guardados por
  canal), `iptv.search` por lista (ADR-055). mu-iptv: *Audio y subtítulos del canal*, distintivos, primera fila
  «Buscar en esta lista». Tests: test_iptv_tracks, test_mu_iptv_tracks, @network La 1.
- Probar a mano:
  ```bash
  bin/mpv-uos   # alt+t → España TV → «Buscar en esta lista» → la 1 → Enter · alt+t → Audio y subtítulos del canal
  ```

### Iteración 3 · 2026-09-30 · H25 · Compartir: salas y retransmisión (puntos 1–2) — hecho (subagente, fusionado)
- mpvd `share/` (puerto 8791, token en el fragmento, cookie por sala, caducidad, intentos, permisos, SSE, HLS con
  VA-API/CPU y WebVTT, hls.js vendorizado) y mu-share (`alt+W`, *Herramientas → Compartir*) (ADR-054).
- Tests: test_share_rooms/http/hls/browser (Chrome sin ventana), test_mu_share.
- Probar a mano (otro equipo de la wifi; antes el `ufw allow … 8791` de NEEDS_HUMAN.md):
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+W → Crear una sala → abre el enlace del QR en el móvil
  ```

### Iteración 3 · 2026-09-30 · H23 · Suscripciones y cadena tras descargar (mpvd) — fusionado; menú en curso
- mpvd `subscriptions/` (`feeds.*`): canales/listas por yt-dlp plano, podcasts RSS con ETag, franja, límite, red
  medida, conservar N, borrar lo visto; cadena: loudnorm 2 pasadas, renombrar, mover a la biblioteca, subtítulos IA
  (ADR-057). Tests: test_feeds, test_feeds_service, test_feeds_chain.
- Probar a mano: `.venv/bin/python -m mpvd call feeds.detect '{"url":"https://www.youtube.com/@BlenderOfficial"}'`

### Iteración nocturna 2026-09-30 · H32 · punto 3 (solo audio en cualquier fuente) — hecho
- mu-ytdl: `alt+a` en archivos locales y directos sin yt-dlp = `vid=no` local del archivo (instantáneo, sin recargar).
- mu-av: *Solo audio al minimizar la ventana* (mu-prefs, desactivado): observa `window-minimized`, quita la pista de
  vídeo del archivo actual y la devuelve al volver.
- Medido (tmp/bench, 1080p60 H.264, 10 s, `mpv --vo=null`): CPU 6,7 s por procesador, 5,8 s VA-API copia, 0,5 s solo
  audio (~13×). Con VA-API directa a la gráfica (sin copia) no se puede medir sin ventana.
- Tests: test_mu_av (alt+a local y siguiente archivo con imagen; minimizar con la opción apagada y encendida).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+a → «Solo audio: el vídeo no se decodifica» · alt+a otra vez
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+v → Solo audio al minimizar la ventana → minimiza y vuelve
  ```

### Iteración nocturna 2026-09-30 · H29 · Subtítulos de vídeos de internet — hecho
- Investigación real (tmp/research-websubs): estructura de `subtitles`/`automatic_captions`, `-orig`, traducciones
  automáticas de YouTube con 429, entradas HLS, formato «rodante» del VTT/SRT automático, argumentos de ytdl_hook.
- mpvd `subs/web.py` + `subs.web.list/fetch` (manuales y automáticos del idioma original, sin traducciones de la web;
  limpieza en cues de dos líneas; caché; reintento con `-J` nuevo si la URL caducó; 429 explicado) (ADR-056).
- mu-subs: *Subtítulos de la web* (con un vídeo de internet abierto): añadir pista, o *Traducir al español (…)*
  (offline, archivo entero); guardar con `alt+S` como cualquier pista.
- Tests: test_subs_web.py (fixtures reales de YouTube: lista, SRT y VTT automáticos, manual; mpvd con servidor local,
  caché, 429; mu-subs headless con el yt-dlp falso), @network vídeo real de 3:27 con automáticos.
- Probar a mano:
  ```bash
  bin/mpv-uos 'https://www.youtube.com/watch?v=UNP03fDSj1U'   # alt+i → Subtítulos de la web → Traducir al español (Inglés (automáticos))
  bin/mpv-uos 'https://www.youtube.com/watch?v=8S0FDjFBj8o'   # alt+i → Subtítulos de la web → Español (manual) · alt+S guarda
  .venv/bin/python -m mpvd call subs.web.list '{"url":"https://www.youtube.com/watch?v=UNP03fDSj1U"}'
  ```

### Iteración nocturna 2026-09-30 · H22 · Biblioteca y subtítulos automáticos — hecho (subagente, fusionado)
- mpvd `library.*`: carpetas elegidas, escaneo incremental en segundo plano (INDEX), películas y series › temporadas ›
  episodios con el progreso de «continuar viendo», búsqueda, `library.continue` (seguir viendo + siguiente episodio),
  `library.next`, carátulas locales o fotograma ffmpeg; TMDB y OpenSubtitles opcionales y desactivados, claves en un
  archivo 0600; subtítulos por hash y luego por nombre, en caché y resincronizados con la voz cuando conviene (ADR-050).
- mu-library (`ctrl+b`): Biblioteca, carpetas, ajustes, subtítulos de internet y siguiente episodio automático con cuenta
  atrás; filas para la pantalla de inicio (mu-menu las muestra) y «Biblioteca» en *Abrir*; entrada en mu-subs.
- Tests: test_library_parse (≈50 nombres reales), test_library_service, test_opensubtitles (API falsa + hash contra la
  referencia oficial), test_mu_library (headless), test_nav[mu_library].
- Pendiente: carátulas no visibles en menús (uosc no pinta imágenes); el cambio diferido a la pista resincronizada no
  se probó con Whisper real.
- Probar a mano:
  ```bash
  bin/mpv-uos      # ctrl+b → Carpetas → Escribir o pegar una ruta… → ~/Vídeos → Series → una serie → temporada → Enter
                   # al final del episodio: «Siguiente episodio en 5 s» · la pantalla de inicio muestra «Siguiente episodio»
  .venv/bin/python -m mpvd call library.status
  .venv/bin/python -m mpvd call library.continue
  ```

### Iteración nocturna 2026-09-30 · H21 · Guía de TV y grabación programada — hecho (subagente, fusionado)
- Verificado: EPG de TDTChannels (`epg/TV.xml.gz`, 537 KB, 184 canales, ~11 000 programas en UTC, ~4 días; 116 de los
  135 tvg-id de la lista casan; 129 canales con programa «ahora»). Detalles en docs/FUENTES_IPTV.md.
- mpvd: `iptv/epg.py` (XMLTV por partes → SQLite, casado por tvg-id o nombre, refresco ≤ cada 12 h;
  `iptv.epg.now/channel/refresh`) e `iptv/schedule.py` (grabaciones persistentes con ffmpeg `-c copy` y las cabeceras
  del canal, parada por reloj, partes, «perdida» al arrancar, avisos; `iptv.schedule.add/list/cancel/remove/parse`).
  mpvd no se cierra con grabaciones pendientes (ADR-049).
- mu-iptv: «ahora: …» en las listas; Tab › Guía de programación / Programar grabación…; «Grabar este programa»;
  «Grabaciones programadas»; paleta «21:30 22:15»; avisos; `alt+G` guía.
- Tests: test_iptv_epg (7 + 1 con red), test_iptv_schedule (15, grabación real de un HLS en directo local),
  test_mu_iptv_epg (2 headless).
- Pendiente: sin arranque de mpvd al iniciar sesión (las grabaciones tras reiniciar necesitan abrir MPV-UOS una vez);
  sin comprobar espacio libre; un solo audio.
- Probar a mano:
  ```bash
  bin/mpv-uos          # alt+t → España · TV: «ahora: …» (la 1.ª vez tarda unos segundos en bajar la guía)
                       # Tab sobre La 1 → Guía de programación → un programa → Grabar este programa (⏺ en la guía)
                       # alt+t → Grabaciones programadas → Programar grabación… → canal → «ahora 2» → Enter
  uv run pytest -q -m network tests/test_iptv_epg.py   # guía real: ≥20 canales con programa ahora
  ```

### Iteración nocturna 2026-09-30 · H20 · Convertir vídeo y audio — hecho (subagente, fusionado)
- mpvd: `mpvd/convert/` (presets → argv exacto de ffmpeg; VA-API por vainfo con `-low_power` y reintento por CPU; cola
  de una en una con nice 10, `.part` + renombrado, historial `conversions.json`, reanudación al arrancar); métodos
  `convert.presets/hw/start/list/get/cancel/retry/remove/clear`, `tasks.list`, `tasks.clear`; eventos `convert` y
  `task` (también de las descargas) a mu_convert (ADR-048).
- mu-convert: menú «Convertir» (preset → opciones → iniciar; tramo con las marcas A-B), «Convertir una carpeta
  entera…», panel «Tareas» en vivo (cancelar, repetir, quitar, abrir carpeta), carpeta y opciones recordadas.
  mu-ytdl: «Convertir…» y «Tareas» en «Descargas y conversión» (⌫ vuelve a él). Teclas `alt+C` y `alt+T`.
- Tests: test_convert_presets, test_convert_service (conversiones reales con ffprobe, tramo, subtítulos, cancelar,
  carpeta, VA-API real y reintento por CPU, reanudación, tasks.list), test_mu_convert.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/voz_es_en.mkv   # alt+C → MP4 compatible → Resolución 720p → Convertir ahora
  bin/mpv-uos tests/fixtures/media/video30.mkv     # l en 0:05 y l en 0:10 → alt+C → GIF animado → Convertir ahora
  bin/mpv-uos   # alt+y → Convertir… → Convertir una carpeta entera… → tests/fixtures/media/serie → Solo audio · MP3
  bin/mpv-uos   # alt+T → Tab sobre una tarea: cancelar / repetir / abrir la carpeta
  .venv/bin/python -m mpvd call convert.hw
  ```

### Iteración nocturna 2026-09-30 · H31 · Formatos de descarga y decodificación del equipo — hecho
- mpvd `hwdecode.py` (vainfo → códecs por hardware, `MPV_UOS_HWDECODE` para fijarlos), `ytdl.hw`, filas de `ytdl.info`
  con `hw` y «fluido en tu equipo» / «exigente (por procesador)»; descargas con `-S vcodec:<mejor por hardware>,res,
  acodec:opus`, MP4 forzado (Opus dentro) con reintento en MKV si ffmpeg no puede; audio original prefiere Opus;
  presets «hasta 1080p (recomendado)» y «original (recomendado)» (ADR-053).
- mu-ytdl: `ytdl-format` local del archivo con los códecs por hardware primero mientras el global sea el de mpv.conf;
  «Decodifica por hardware» en Estado de yt-dlp. USO: tamaños orientativos por hora.
- Tests: test_hwdecode.py (vainfo real de este portátil y uno moderno, claves de códec, etiquetas, -S, formato de
  reproducción, mpv.conf = FACTORY_FORMAT), presets/descargas actualizados (+ reintento MKV), test_mu_ytdl_batch
  (formato por hardware y respeto al del usuario), @network descarga real → .mp4 con Opus.
- Probar a mano:
  ```bash
  .venv/bin/python -m mpvd call ytdl.hw
  bin/mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+q: cada vídeo con «fluido en tu equipo» o «exigente»
  bin/mpv-uos 'https://www.youtube.com/watch?v=jNQXAC9IVRw'   # alt+d → Vídeo · hasta 1080p → ffprobe: mp4 con opus
  ```

### Iteración nocturna 2026-09-30 · H33 · Identidad (logo; nombre centralizado) — hecho
- `brand.json` (nombre, id, carpeta, paleta) leído por `mpvd/brand.py`, `mu/brand.lua`, install.sh y la PWA (ADR-052).
  Usos migrados: títulos del menú y migas, MPRIS, MCP, User-Agent, carpetas `<Vídeos>/MPV-UOS`, `Name=` del escritorio.
- Logo: icono de escritorio (+ `mpv-uos-symbolic` monocromo), PWA (SVG + PNG 192/512, colores de tinta y azul señal),
  variantes `logo-sin-fondo.svg` y `logo-mono.svg`; paleta en uosc.conf; ● REC en ámbar.
- Tests: test_brand.py (fuente única, respaldo si el JSON se rompe, Lua lee otro nombre, iconos y manifiesto),
  test_install.py (icono simbólico y `Name=` de brand.json).
- Probar a mano:
  ```bash
  tools/install.sh --no-sync --no-vendor   # el menú de aplicaciones muestra el logo nuevo
  bin/mpv-uos tests/fixtures/media/video30.mkv   # barra y menús con la paleta; alt+m → título MPV-UOS
  bin/mpv-uos   # alt+z → en el móvil, «Añadir a pantalla de inicio» usa el logo
  ```

### Iteración nocturna 2026-09-30 · H24 · Escritorio y sonido — hecho
- mpvd `mpris.py`: MPRIS por cada mpv (Play/Pause/PlayPause/Stop/Next/Previous/Seek/SetPosition/OpenUri; Metadata,
  PlaybackStatus, Volume, Rate, LoopStatus, Shuffle, Fullscreen; PropertiesChanged y Seeked); `mpris.status`;
  extra `desktop` (jeepney) en pyproject, check.sh e install.sh (ADR-051). Tests con `dbus-daemon` privado.
- mu-av: *Volumen igualado* (dynaudnorm lento) y *Ecualizador* con 7 perfiles, recordados en mu-prefs;
  `script-message mu-av-eq <perfil>`.
- Tests: test_mpris.py (bus privado: nombre, metadatos, controles, señales, volumen, repetición, velocidad),
  test_mu_av.py (volumen igualado, los 7 perfiles sin errores de lavfi, submenú y recuerdo).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # aparece en los controles multimedia de KDE; teclas ⏯ ⏭ del teclado
  gdbus call --session --dest org.mpris.MediaPlayer2.mpv_uos.instance$(pgrep -n mpv) \
    --object-path /org/mpris/MediaPlayer2 --method org.mpris.MediaPlayer2.Player.PlayPause
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+v → Volumen igualado · Ecualizador → Más graves
  .venv/bin/python -m mpvd call mpris.status
  ```

### Iteración nocturna 2026-09-30 (modo a tope) · H20 + H21 + H22 en paralelo, H24 en la sesión — plan
- Subagentes en worktrees: H20 Convertir (mpvd/convert + mu-convert + panel «Tareas» con descargas), H21 Guía de TV
  (EPG XMLTV de TDTChannels, now/next, parrilla) y grabación programada (ffmpeg en mpvd, persistente), H22 Biblioteca
  (escaneo, películas/series, carátulas, seguir viendo, siguiente episodio) y OpenSubtitles por hash (desactivado).
- Coordinador: integra textos compartidos (input.conf, ATAJOS, mu-menu, ADR, USO), fusiona y pasa check.sh una vez.
- Sesión: H24 · MPRIS (mpvd ↔ D-Bus de sesión), volumen igualado entre vídeos y ecualizador sencillo en mu-av.

### Iteración nocturna 2026-09-30 · H19 · parte 3 (nightly, suplantación, sesión del navegador) — H19 hecho
- mpvd: nightly de yt-dlp en `vendor/bin/yt-dlp-nightly` (bajado y verificado por SHA bajo demanda, refrescado con la
  comprobación diaria); una descarga que falla por algo que no es de disponibilidad se repite UNA vez con él
  (`worth_nightly`); `ytdl.nightly`; ajuste `cookies_browser` (`--cookies-from-browser`, desactivado, validado) aplicado a
  descargas, `-J` y ytdl_hook; `status` informa de `impersonate` (curl_cffi) y del nightly. Extra `impersonate` en
  pyproject (curl_cffi 0.16) instalado por check.sh e `install.sh --extras` (ADR-047).
- mu-ytdl: al reproducir, una URL que la estable no abre se recarga una vez con el nightly primero en `ytdl_path` y
  luego vuelve la estable; Ajustes de descarga › *Usar mi sesión del navegador*; Estado de yt-dlp muestra nightly y
  suplantación.
- Tests: test_ytdl_downloads (reintento con nightly, sin reintento en privados ni con el ajuste apagado),
  test_mu_ytdl_batch (reproducción con reintento único, sesión del navegador), @network perfil de TikTok como lista.
- Probar a mano:
  ```bash
  bin/mpv-uos 'https://ok.ru/video/<id>'        # «Probando con yt-dlp nightly…» y reproduce
  bin/mpv-uos   # alt+y → Descargar de una lista o canal… → https://www.tiktok.com/@nasa → casillas → Descargar
  bin/mpv-uos   # alt+y → Ajustes de descarga → Usar mi sesión del navegador (Enter cambia de navegador)
  .venv/bin/python -m mpvd call ytdl.nightly
  ```

### Iteración nocturna 2026-09-30 · H19 · parte 2 (subtítulos SRT al descargar)
- `subs_mode` embed/file/only, idiomas `orig,es.*,en.*` (mpvd resuelve «orig» con el idioma del vídeo), preset *Solo
  subtítulos (SRT)* y rutas de los `.srt` por `after_video` (ADR-046). Menú Descargar: *Subtítulos* (no / dentro / SRT
  aparte) e *Idiomas* (originales + es + en, solo el original, español, inglés, todos).
- Tests: presets (modos, «orig»), downloads (solo SRT y SRT aparte con el yt-dlp falso), test_mu_ytdl_batch (menú).
  Ajustados: test_mu_ytdl_palettes (nueva entrada) y test_mu_subs (acepta «OPUS-MT listo»). check: 334 + 8 en verde.
- Probar a mano: `bin/mpv-uos 'https://www.youtube.com/watch?v=jNQXAC9IVRw'` → `alt+d` → *Solo subtítulos (SRT)*.

### Iteración nocturna 2026-09-30 · H19 · Gestor de descargas — parte 1 (varias URL, listas, ajustes, cola)
- mpvd: `ytdl.download.batch` (texto, lista o `.txt`), `sections`/`archive`/`list_folder` en DownloadSpec, ajustes
  `rate_limit`, `archive`, `list_folders`; plantilla por lista; la cola pendiente se reanuda al arrancar (ADR-045).
- mu-ytdl: *Descargar varias URL…*, *Descargar de una lista o canal…* (casillas, todos marcados al principio),
  *Ajustes de descarga* (a la vez, límite, no repetir, carpeta por lista).
- Tests: test_ytdl_downloads (lote, límite, archivo, lista, reanudar tras reinicio), test_mu_ytdl_batch (interfaz).
  `tools/check.sh`: 331 sin red + 8 con red en verde.
- Probar a mano:
  ```bash
  bin/mpv-uos   # alt+y → Descargar varias URL… → ctrl+v con varios enlaces → «Descargar N enlaces · vídeo»
                # alt+y → Descargar de una lista o canal… → URL de una lista → Ver la lista y elegir → desmarcar → Descargar
  ```

### Iteración nocturna 2026-09-30 · H18 · Botón «Grabar» unificado — hecho
- Verificado (subagente, notas en tmp/rec/NOTES.md): `--download-sections "*A-B"` corta con ffmpeg sin recodificar; con
  webm/opus de YouTube el tramo sale mal, con H.264/AAC bien (±0,03 s); `dump-cache`/`stream-record` de mpv pierden
  fotogramas B con fuentes MKV; en ffmpeg, `-avoid_negative_ts make_zero` rompe la lista de edición de mp4.
- `mu-record` (script nuevo, ADR-044): botón ● y `alt+r`; capturas con/sin subtítulos; grabar desde ahora (vídeo o solo
  audio) hasta detener; recortar tramo con las marcas A-B; punto rojo + contador «● REC m:ss» y contador en el botón;
  carpeta recordada (por defecto `~/Vídeos/MPV-UOS/Grabaciones`). Directos → `stream-record` (+ `record.audio` de mpvd
  para quedarse con el audio); internet → yt-dlp `sections` (formato H.264/AAC, nombre con el tramo); locales →
  `study.clip` `mp4-copy`/`mkv-copy`/`audio-copy` (nuevo). Tipo de fuente fijado al empezar (yt-dlp dice si es directo).
- De paso: `mp4-copy`/`mkv-copy` ya no usan `make_zero` (el mp4 empieza justo en A); mu-subs ya no usa `sub-reload`
  (volvía a seleccionar la pista IA de forma asíncrona y pisaba la elegida: el test_mu_subs intermitente).
- Tests: test_record (3), test_mu_record (local vídeo/audio/tramo, directo con contador y solo audio, tramo de internet
  con el yt-dlp falso), presets (sections), nav (mu_record, mu_notes). `tools/check.sh`: 329 sin red + 8 con red.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # ● → Grabar desde ahora … Detener · Recortar un tramo (marcas) · alt+r
  bin/mpv-uos                                     # alt+t → un canal → alt+r (punto rojo) → alt+r: ~/Vídeos/MPV-UOS/Grabaciones
  bin/mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # ● → Grabar desde ahora … Detener: descarga solo ese tramo
  ```

### Iteración nocturna 2026-09-30 · H17 · «Mis notas» — hecho
- `mpvd/notes.py` (ADR-043): un Markdown por vídeo con el título como nombre y cabecera `titulo/video/clave` (la clave de
  contenido de «continuar viendo»), notas por tiempo, `notes.get/edit/delete/export`, migración de los archivos de H8.
- `mu-notes` (script nuevo, `alt+B`, *Herramientas › Mis notas*): notas de este vídeo y de todos, Enter salta al minuto
  (abre el vídeo si es otro), Tab edita (cuadro de texto) o borra, exportar junto al vídeo o a una carpeta recordada.
- Enlaces `mpv-uos://open?path=…&t=…`: bin/mpv-uos los abre en su minuto (grupos `--{ --start=T … --}`), un hook
  `on_load` los resuelve dentro de mpv y `tools/install.sh` registra `x-scheme-handler/mpv-uos` (sin sudo).
- Tests: test_notes (5), test_mu_notes (menú, saltar, editar, exportar ×2, borrar, ⌫ hasta el principal, enlace dentro de
  mpv), test_launcher y test_install (enlaces). `tools/check.sh`: 320 sin red + 8 con red en verde.
- Probar a mano (para los enlaces fuera de MPV-UOS hay que reinstalar una vez: `tools/install.sh --no-sync --no-vendor`):
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv   # alt+b «hola» Enter · alt+B → Notas de este vídeo → Enter / Tab
  ls ~/.local/share/mpv-uos/notas/               # «video30.md» con enlaces mpv-uos://
  xdg-open 'mpv-uos://open?path='"$PWD"'/tests/fixtures/media/video30.mkv&t=12'   # tras reinstalar: abre en 0:12
  ```

### Iteración nocturna 2026-09-30 · H16 · Sincronía de los subtítulos IA — hecho
- Verificado (subagente + pruebas propias, docs/WHISPER.md): `-ojf` da tiempos por token; `--dtw` solo funciona con `-nfa`;
  con `--vad` los tokens quedan en tiempo «solo voz» y el log (sin `-np`) trae la tabla para devolverlos.
- `mpvd/asr/timing.py` (ADR-042): palabras desde tokens, tabla VAD → tiempo original, tramos de Silero partidos por pausas
  de una VAD de energía, huecos asignados al mejor límite entre palabras, agrupación (pausas, comas, líneas de 42, máx. 84
  caracteres / 7 s, cortes en tiempos reales de palabra), bordes a la voz y reglas de lectura (0,9 s mín., 17 car/s, 80 ms,
  sin solapes). Sin modelo VAD: `--dtw <tamaño> -nfa` + VAD de energía. Caché de transcripciones → versión 2.
- Medido (`tests/test_asr_timing_real.py`, frases en posiciones conocidas): inicio medio 27 ms con Silero y 4 ms con DTW
  (criterio < 150 ms); antes, un solo subtítulo de 0,8 a 10,3 s juntaba tres frases.
- test_subs_service: el SRT «retrasado» del test pasa a ser un retraso constante de 2,5 s sobre la voz real (antes
  cuadraba solo con los tiempos imprecisos de whisper). `tools/check.sh`: 312 sin red + 8 con red en verde.
- Probar a mano:
  ```bash
  uv run pytest tests/test_asr_timing.py tests/test_asr_timing_real.py -q
  bin/mpv-uos tests/fixtures/media/voz_es_en.mkv    # alt+c: los subtítulos IA aparecen y se van con la voz
  # una película ya subtitulada antes de H16 se vuelve a transcribir al abrirla (caché v2)
  ```

### Iteración nocturna 2026-09-30 · H15 · Interfaz y navegación — hecho
- `script-modules/mu/nav.lua` (ADR-041): migas en el título, fila «Atrás», ⌫/←/botón atrás del ratón y traspaso entre scripts
  (`mu-nav-open` / `mu-nav-return`); integrado en mu-menu, mu-iptv, mu-ytdl, mu-subs, mu-av, mu-intro, mu-study y mu-remote.
  Las paletas (URL, YouTube, canal) siguen cerrándose con ⌫ vacío.
- Menú principal: *Continuar viendo* (si hay algo) + Abrir, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar,
  Herramientas, Preferencias; `?` ayuda; Preferencias › «Pausar con un clic en el vídeo» (desactivado); botón ● Grabar en la barra
  (abre la categoría Grabar hasta H18). Raíces renombradas: «Descargas y conversión» (yt-dlp), «Filtros de imagen y sonido».
- uosc.conf: barra reducida (⏭ solo dentro de un segmento), `menu_item_height=42`.
- De paso: mu-subs publicaba «traducción lista» antes de añadir la pista (carrera que hacía fallar test_mu_subs en el check).
- Tests: `tests/test_nav.py` (9: categorías y migas, entrar en un módulo desde una categoría y volver con la fila Atrás, ⌫ y ← reales
  por uosc, Esc, ayuda, cada uno de los 7 módulos abre con su tecla → ⌫ al menú principal → Esc, clic-pausa opcional).
  `tools/check.sh`: 301 sin red + 8 con red en verde.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv
  # clic derecho → Subtítulos → «Subtítulos IA y traducción»: el título dice «MPV-UOS › Subtítulos › Subtítulos IA»;
  #   ⌫ vuelve a Subtítulos, ← otra vez al principal, Esc cierra; clic en «Atrás» hace lo mismo con el ratón
  # alt+t (TV) y ⌫ → menú principal · ? → ayuda · Preferencias → «Pausar con un clic en el vídeo» y clic sobre el vídeo
  uv run pytest tests/test_nav.py -q
  ```
#### H15 — plan
- Verificado en uosc 5.13 (vendorizado): el título solo se pinta en la raíz de un menú; ⌫ y el botón «atrás» del ratón en la raíz
  mandan `{type:'back'}`; ← en la raíz (sin submenú padre ni búsqueda) llega como `{type:'key', id:'left'}`; `script-binding
  uosc/menu-back` vuelve al submenú padre o, en la raíz, manda `back`; `selected_index` del JSON solo cuenta en la raíz.
- `script-modules/mu/nav.lua`: migas («MPV-UOS › TV y radio › España»), fila «‹ Atrás» primera en cada vista (con la selección de
  teclado en la fila siguiente), ←/⌫/fila = atrás, y al vaciar la pila de un módulo vuelve a quien lo abrió (por defecto el menú
  principal) con `script-message-to mu_menu mu-nav-return <vista>`. El menú principal abre los módulos con `mu-nav-open` y les pasa
  sus migas. `user-data/mu/nav` publica el título y el script activos (tests).
- mu-menu: raíz en 8 categorías con icono (Abrir, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar,
  Herramientas, Preferencias); `?` abre una ayuda en pantalla; clic en el vídeo para pausar como preferencia (desactivada).
- Barra de uosc: reproducción, subtítulos, audio, velocidad, grabar, menú y pantalla completa; letra del menú mayor.
- Tests headless de navegación (entrar, atrás, cerrar) en todos los módulos.
### Iteración 5 · 2026-09-29
#### H13 · Cierre — hecho (commit "H13: cierre")
- `tools/install.sh` (lanzador `~/.local/bin/mpv-uos` → checkout, `.desktop` validado con tipos MIME y `--wayland-app-id=mpv-uos`,
  icono SVG, `--xdg`, `--extras`, `--default`, `--dry-run`, `--uninstall` que solo borra lo que lleva su marca; se niega a
  sobrescribir un `mpv-uos` ajeno). Tests (tests/test_install.py) en prefijos de `tmp/`: instalación, idempotencia, `--version` a
  través del lanzador, variables XDG, desinstalación selectiva y dry-run. No se ha ejecutado contra el `~/.local` real.
- `docs/USO.md` (guía por tareas), README (instalación, mando, guía), `docs/PLATAFORMAS.md` (tabla por componente, macOS/Windows).
- Probar a mano:
  ```bash
  tools/install.sh --dry-run          # qué haría
  tools/install.sh && mpv-uos --version && tools/install.sh --uninstall
  ```
#### H13 · Cierre — plan
- `tools/install.sh`: instalación de usuario sin sudo (lanzador en `~/.local/bin` que apunta al checkout, `.desktop` validado con
  tipos MIME, icono SVG, `--xdg` para caché/datos en rutas XDG, `--default` con xdg-mime, `--dry-run`, `--uninstall` que solo borra
  lo que lleva su marca). Tests en prefijos de `tmp/` (nunca el `~/.local` real).
- `docs/USO.md` (guía por tareas), README (instalación, mando, enlace a la guía), `docs/PLATAFORMAS.md` (tabla por componente,
  instalación macOS/Windows), resumen final y `ESTADO_GLOBAL: COMPLETADO` en PROGRESS.md.
#### H12 · Mando QR/PWA — hecho (commit "H12: mando QR/PWA")
- Verificación real (subagente → docs/REMOTE_API.md): `overlay-add` exige fichero BGRA y coordenadas de pantalla → QR como overlay ASS
  (`mp.create_osd_overlay`); ni `qrencode` ni `segno` instalados → QR en Python puro con tablas copiadas de una implementación de
  referencia; `ufw` activo con entrada DROP (ver NEEDS_HUMAN.md).
- `mpvd/remote/`: `qr.py` (modo byte, v1–10, nivel M, 8 máscaras), `http.py` (HTTP/1.1 + SSE sobre asyncio), `service.py`
  (`remote.status/start/stop/pair/forget`; token de un solo uso de 10 min en `#t=` → cookie HMAC; lista blanca de órdenes; estado
  por SSE ≤2 Hz; móviles emparejados en `<datos>/remote.json`), `www/` (PWA: control, canales, búsqueda, recientes, más). ADR-032.
- `mu-remote/main.lua`: `alt+z` QR + URL (se oculta a los 120 s), `alt+Z` menú (estado, móviles, olvidar, arrancar/detener), entrada
  en el menú raíz; estado en `user-data/mu/remote`. `SessionManager` avisa a oyentes al abrir/cerrar sesiones (el mando sigue al mpv
  más reciente si se cierra el que mostró el QR).
- Tests (tests/test_remote.py, 17): QR decodificado con zbarimg en todas las versiones y máscaras, servidor HTTP y SSE, emparejamiento
  (token inválido/reutilizado rechazado, cookie falsa rechazada, Origin ajeno rechazado), órdenes aplicadas en mpv headless, SSE con
  time-pos, persistencia y desemparejar, mu-remote headless (overlay y menú). Docs: docs/REMOTE.md.
- Probar a mano (con el puerto abierto en ufw):
  ```bash
  bin/mpv-uos tests/fixtures/media/chapters.mkv     # alt+z → escanear el QR con el móvil (misma wifi); alt+Z menú
  .venv/bin/python -m mpvd call remote.status
  curl -s http://127.0.0.1:8790/api/state            # sin emparejar → 401
  ```
### Iteración 4 · 2026-09-29
#### H11 · Estudio — hecho (commit "H11: modo estudio")
- Verificación real (subagente → docs/ESTUDIO.md): sub-start/sub-end sin sub-delay y `unavailable` sin cue; `ab-loop-a` = "no";
  speed en caliente sin errores; ffmpeg copy corta en keyframe; silencedetect -30 dB/0,3–0,5 s para pausas de voz; VAD Silero disponible.
- `mpvd/study/`: `clips.py` (argv por formato mp4/mp4-copy/mkv-copy/gif/mp3/opus/wav, `-progress pipe:1`, nombres
  `<stem> [hh.mm.ss-hh.mm.ss].ext`), `silence.py` (mapa de silencios con padding adaptativo), `service.py` (`study.formats`,
  `study.clip` job con eventos `clip` a todas las sesiones e historial `clips.json`, `study.clips.list/cancel`, `study.silences`
  cacheado por hash). ADR-031.
- `mu-study/main.lua`: alt+e menú Estudio; alt+w repetir línea (alt+←/→ anterior/siguiente; se apaga si el usuario quita el A-B);
  alt+g velocidad inteligente (opciones `mu-study-silence_speed/silence_db/silence_min`); alt+b nota (cuadro de búsqueda de uosc
  como campo de texto + cita del subtítulo → notes.add); alt+u clip del A-B o de la línea (formato por menú; abrir el clip
  terminado desde el menú). Estado en `user-data/mu/study`; mensajes `mu-study-note/clip/smart/repeat`.
- Tests (tests/test_study.py): argv y nombres, 5 formatos reales con ffprobe, métodos vía daemon (cola, historial, errores,
  silencios cacheados), mu-study headless (bucle con sub-delay, apagado al quitar A-B, nota con cita y enlace, clip mp3 del A-B
  con evento, velocidad ×2,5 en pausa y vuelta a ×1,25, menú).
- Probar a mano:
  ```bash
  bin/mpv-uos pelicula.mkv    # con subtítulos: alt+w repite la línea; alt+g acelera silencios; alt+b nota; l l marca A-B y alt+u exporta
  .venv/bin/python -m mpvd call study.clips.list
  cat "$(.venv/bin/python -m mpvd call notes.list | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["file"])')"
  ```
#### H10 · Búsqueda semántica y capítulos automáticos — hecho (commit "H9+H10")
- `mpvd/semantic/`: `embed.py` (Embedder ONNX + sentencepiece, 2 hilos, descarga verificada del modelo a vendor/models/embed),
  `index.py` (frases desde cues, blob float32, búsqueda coseno + bonus literal, capítulos por cambio de tema, ffmetadata),
  `service.py` (`semantic.status/models.download/index/search/chapters`; caché por hash; fallback literal; indexado en segundo
  plano), `fake.py` (embedder de test). `asr.inject` (gancho de test). Extra `semantic` en pyproject; check.sh lo instala.
- mu-menu: sección "Diálogo (semántico)" en la paleta (Enter → seek). mu-subs: "Capítulos por tema (IA)" (aplica/quita
  `chapter-list`; opciones `mu-subs-chapter_min_seconds`, `mu-subs-chapter_window`; mensaje `mu-subs-chapters yes|no`).
- docs/SEMANTICA.md (verificación real: paquetes, modelos HF con SHA, benchmark), ADR-030, README, ATAJOS.
- Tests (tests/test_semantic.py): unión de frases, búsqueda y blob, capítulos (3 temas → 3 capítulos, homogéneo → 0),
  modelo real ES↔EN (se omite sin extra/modelo), métodos vía daemon con embedder falso, paleta + capítulos en mpv headless.
- Probar a mano:
  ```bash
  bin/mpv-uos charla.mkv        # alt+c subtítulos IA hasta el final; alt+p y escribe "cuando hablan de X" → sección Diálogo
                                # alt+i → "Capítulos por tema (IA)" → capítulos en la barra de uosc
  .venv/bin/python -m mpvd call semantic.chapters '{"path":"'$PWD'/charla.mkv"}'
  ```
#### H10 · Búsqueda semántica y capítulos automáticos — plan
- Verificado (subagente → docs/SEMANTICA.md): `onnxruntime` 1.30 (wheel cp312 23,6 MB; numpy, flatbuffers, protobuf, packaging) +
  `sentencepiece` (ya en `translate`) bastan; `tokenizers` arrastra huggingface-hub y 270 MB de RSS, se descarta. Modelo
  `Xenova/paraphrase-multilingual-MiniLM-L12-v2` `onnx/model_quantized.onnx` (118 MB, u8u8: 2× más rápido que el quint8_avx2 en
  esta CPU sin VNNI) + `sentencepiece.bpe.model` (5 MB) del repo sentence-transformers; dim 384, mean pooling + L2, max 128 tokens,
  sin prefijos; medido: carga 1,4 s, ~6 ms por segmento Whisper con 2 hilos, RSS 270 MB; coseno ES↔EN 0,98/0,86 vs <0,09 no
  relacionadas. sqlite-vec funciona pero no hace falta (<2 000 vectores por archivo → NumPy). `chapter-list` de mpv 0.41 es
  escribible por IPC (probado: set_property chapter-list [{title,time}]).
- mpvd `semantic/`: `embed.py` (Embedder ONNX+spm portado del benchmark, hilos = min(2, nproc-1), descarga de modelo con SHA-256 como
  av.py, `available()`), `index.py` (frases = segmentos Whisper agrupados hasta ~25 palabras; vectores float32 en blob de la caché por
  (hash, "embed", modelo, versión, params); búsqueda coseno + fusión con coincidencia textual; capítulos: ventanas 45 s solape 50 %,
  d=1−cos, media móvil 3, máximos > percentil 85, mínimo 180 s, título = frase más cercana al centroide), `service.py`
  (`semantic.status`, `semantic.models.download`, `semantic.index {path}` job PRECOMPUTE, `semantic.search {q, path?, k}`,
  `semantic.chapters {path, min_seconds?, percentile?}` con caché y exportación ffmetadata).
- mu-menu paleta: sección "Diálogo" (`semantic.search` del archivo actual; si no hay índice cae a `asr.search`); Enter → seek.
  mu-subs: entrada "Capítulos por tema (IA)" → `semantic.chapters` → `chapter-list`; "Quitar capítulos IA" restaura los originales.
- Tests: embedder falso determinista (bolsa de palabras → vector) para índice/búsqueda/capítulos; test con modelo real si está en
  vendor/models/embed y onnxruntime importable (búsqueda cruzada ES/EN sobre voz_es/voz_en); integración headless (paleta "Diálogo",
  capítulos aplicados en `chapter-list`).
#### H9 · Salto de intro/créditos — hecho (commit "H9+H10")
- `mpvd/intro/`: `fingerprint.py` (huella `fpcalc -raw -json` por ventana sobre WAV extraído con ffmpeg; `match()` = votos por
  diagonal + rachas Hamming ≤6 con recorte de bordes planos), `detect.py` (`silencedetect`/`blackdetect` de ffmpeg → cortes y `snap`),
  `service.py` (`intro.segments` cache→análisis en segundo plano con evento push, `intro.analyze` (`wait`), `intro.export`;
  vecinos por número de episodio (S01E02, 1x02, ep02, 02), consenso por mediana, exportación a `<carpeta>/.mpv-uos/segments.json`).
  `capabilities.services.intro` = hay `fpcalc`. ADR-029.
- `mu-intro/main.lua`: pide segmentos en file-loaded, sondeo de time-pos (0,5 s) → botón `mu-skip` en la barra de uosc con badge
  intro/fin + aviso OSD; `alt+k` salta (intro → fin del tramo; créditos → siguiente elemento de la lista o final; fuera de tramo →
  fin del siguiente); `alt+j` menú (segmentos con tiempos, saltar ahora, salto automático de intro/créditos, activar/desactivar,
  volver a analizar); mensajes `mu-intro-skip <tipo>`, `mu-intro-set <clave> yes|no`, `mu-intro-refresh`; estado en
  `user-data/mu/intro`. Entrada "Saltar intro y créditos" en el menú raíz; opciones `mu-intro-*` (auto_skip_intro/credits, enabled).
- tools/make_test_media.sh genera `serie/ep01..03.mkv` (38 s: 1,5 s negro, intro común de 8 s, cuerpo distinto de 22 s,
  0,5 s de silencio, créditos comunes de 6 s) con segmentos esperados en manifest.json.
- Tests (tests/test_intro.py, se omiten sin fpcalc): vecinos/snap, huellas ep01↔ep02 (intro y créditos encontrados, cuerpos no
  coinciden), servicio (análisis → caché → segments.json → intro.export, segundo episodio rápido por caché), mu-intro headless
  (segmentos, menú, salto en la intro, créditos → siguiente episodio, salto automático).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/serie/ep01.mkv tests/fixtures/media/serie/ep02.mkv   # a los ~2 s: "Intro · alt+k"; alt+j menú
  .venv/bin/python -m mpvd call intro.analyze '{"path":"'$PWD'/tests/fixtures/media/serie/ep02.mkv","wait":true}'
  cat tests/fixtures/media/serie/.mpv-uos/segments.json
  ```
### Iteración 3 · 2026-09-29
#### H8 · MCP rico — hecho (commit "H8: MCP")
- `mpvd/mcp.py`: servidor MCP stdio sin dependencias (ADR-028): initialize/ping/tools/resources/prompts; 11 tools (status, play,
  pause, resume, seek, search_dialogue, list_channels, play_channel, download, add_note, subtitles_ai) y resources
  `mpv://transcript/<id>` y `mpv://notes/<clave>`; errores de tool como `isError`. CLI `python -m mpvd mcp [--session] [--yes]`
  (arranca el daemon si hace falta; logs por stderr).
- mpvd: `mpvd/control.py` (`session.get/set/command/confirm`, `notes.add/list/read` con enlaces `mpv://seek?t=`), `asr.search`,
  `sessions.all()`. mu-menu: diálogo `mu-confirm <token> <texto>` (uosc Sí/No, 15 s, publica `user-data/mu/confirm{_request}`).
- `.mcp.json.example`, docs/MCP.md, README.
- Tests: test_mcp (handshake, tools/list, status idle, play/pause/resume/seek autoconfirmados, append, notas + resource, errores,
  search_dialogue sin transcripción lanza una; diálogo real de confirmación: sí → salta, no → no salta).
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv &
  cp .mcp.json.example .mcp.json   # y en Claude Code: /mcp → mpv-uos → status, "salta al minuto 0:20" (confirma en la pantalla de mpv)
  printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"sh","version":"0"}}}' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"status","arguments":{}}}' | .venv/bin/python -m mpvd mcp
  ```
#### H8 · MCP rico — plan
- Verificado: el SDK oficial `mcp` 2.2.0 arrastra ~30 paquetes (pydantic, starlette, uvicorn, cryptography, opentelemetry…): se
  descarta (ADR-028). Se implementa el protocolo MCP mínimo a mano (JSON-RPC 2.0 por stdio, una línea por mensaje; spec 2025-06-18:
  initialize, notifications/initialized, ping, tools/list, tools/call, resources/list, resources/read, prompts/list vacío).
- mpvd: métodos nuevos `session.get/set/command` (control de una sesión de mpv por su id o la actual), `asr.search` (búsqueda sin
  acentos en los segmentos transcritos), `notes.add/list` (Markdown en `<data_dir>/notas/<clave>.md` con enlaces de tiempo).
  mu-menu: `mu-confirm <token> <texto>` abre un diálogo uosc Sí/No y publica `user-data/mu/confirm = {token, answer}`.
- `python -m mpvd mcp`: tools status, play, pause, resume, seek, search_dialogue, list_channels, play_channel, download, add_note;
  resources `mpv://transcript/<clave>`; confirmación en OSD (15 s) para play/seek/play_channel/download salvo `MPVD_MCP_AUTOCONFIRM=1`.
  `.mcp.json.example` + README. Tests: cliente MCP mínimo por stdio contra el daemon de test + mpv headless.
#### H7 · Sonido e imagen — hecho (commit "H7: sonido e imagen")
- `mpvd/av.py`: servicio `av.models` / `av.models.download` / `av.models.path` (RNNoise sh/bd y HRTF MIT KEMAR fijados por SHA-256 en
  vendor.lock; descarga con progreso por eventos `av-model`). Modelos ya descargados en vendor/models/{rnnoise,sofa}.
- `mu-av/main.lua`: menú "Sonido e imagen" (alt+v, botón en la barra, entrada en el menú raíz): diálogo claro, modo noche (alt+n),
  reducción de ruido (arnndn→afftdn), binaural (sofalizer→crossfeed), protección fotosensible (vf), perfil ligero (guarda/restaura),
  diagnóstico de tirones con contadores y consejos, vista de modelos con descarga, "quitar todos". Filtros etiquetados `@mu-<x>`.
- docs/AUDIO_VIDEO.md (grafos validados, propiedades, alternativas), ADR-027, README, ATAJOS.
- Tests: test_mu_av (cada filtro añade su grafo lavfi y la reproducción sigue sin errores lavfi, RNNoise/SOFA usados si están,
  toggles, perfil ligero restaura, menú raíz/diagnóstico/quitar todos), av.models y errores.
- Probar a mano:
  ```bash
  bin/mpv-uos pelicula.mkv        # alt+v → activa "Diálogo claro" y "Modo noche"; alt+n alterna noche; Diagnóstico de tirones
  .venv/bin/python -m mpvd call av.models
  ```
#### H7 · Sonido e imagen — plan
- Verificado (mpv 0.41 `--af=help`/`--vf=help` + ejecución headless con `--end=3` sin errores en el log): lavfi disponible con
  acompressor, afftdn, alimiter, anlmdn, arnndn, compand, crossfeed, deesser, dynaudnorm, equalizer, haas, highpass, loudnorm, lowpass,
  sofalizer (libmysofa compilado), speechnorm, stereotools; vídeo: photosensitivity, deband, deflicker, eq, hqdn3d, nlmeans, tmix,
  unsharp. Filtros con etiqueta `@mu-<x>:lavfi=[grafo]` (`af add/remove/toggle`). Propiedades de diagnóstico: frame-drop-count,
  decoder-frame-drop-count, mistimed-frame-count, vo-delayed-frame-count, estimated-vf-fps, container-fps, display-fps,
  estimated-display-fps, hwdec-current, current-vo, video-out-params.
- Modelos descargados y fijados por SHA-256 en vendor.lock: RNNoise `sh.rnnn` (somnolent-hogwash, general) y `bd.rnnn`
  (beguiling-drafter, voz), ~300 KB cada uno (GregorR/rnnoise-models, BSD); HRTF `mit_kemar_normal_pinna.sofa` (1,1 MB, sofacoustics.org).
- Grafos: diálogo claro `highpass=f=70,dynaudnorm=f=250:g=11:p=0.85:m=8,equalizer=f=2800:t=q:w=1.2:g=2.5`; modo noche
  `acompressor=threshold=-24dB:ratio=6:attack=5:release=400:makeup=4dB,alimiter=limit=0.7`; ruido `arnndn=m=<rnnn>:mix=0.9`
  (sin modelo: `afftdn=nr=12:nf=-40`); binaural `sofalizer=sofa=<sofa>:type=freq` (sin SOFA: `crossfeed=strength=0.5:range=0.5`);
  fotosensible `vf @mu-photo:lavfi=[photosensitivity=frames=30:threshold=1:bypass=0]`.
- Pasos: `mpvd/av.py` (`av.models`, `av.models.download` con eventos `av-model`) → `mu-av/main.lua` (menú "Sonido e imagen" alt+v,
  modo noche alt+n, toggles por etiqueta, diagnóstico de tirones con recomendaciones y "perfil ligero") → tests headless (cada filtro
  activo en `af`/`vf`, reproducción sigue, sin errores; diagnóstico; perfil ligero) → docs/AUDIO_VIDEO.md, ADR-027, README, ATAJOS.
#### H6 · Sincronía, traducción y duales — hecho (commit "H6: sincronía, traducción y duales")
- `mpvd/subs/formats.py` (SRT/VTT/ASS, BOM/utf-8/utf-16/cp1252, etiquetas fuera), `resync.py` (ADR-026: emparejamiento por palabras en
  banda ±120 s, cadena monótona de máximo peso, recta robusta Theil–Sen por ventana con cortes y extrapolación), `translate.py`
  (ADR-025: paquetes Argos sobre CTranslate2 + sentencepiece, pivote por inglés, agrupación de cues sin tocar tiempos, descarga con
  SHA-256 e índice oficial cacheado), `service.py` (`subs.info/shift/resync/translate/translate.models/translate.download/translate.remove`,
  eventos push, caché de traducciones por hash del SRT).
- mu-subs: "Resincronizar la pista externa con la IA" (alt+x; si no hay transcripción la lanza y reintenta al terminar),
  "Traducir la pista seleccionada a…" (paquete que falte → se descarga y se traduce solo), "Duales: original arriba + traducción abajo"
  (`secondary-sid`), pista "Traducción (xx)"; `sub-reload` solo mientras la pista IA está seleccionada (no roba la selección).
- pyproject: extra opcional `translate` (ctranslate2 4.8, sentencepiece 0.2; .venv ≈206 MB); check.sh lo instala si falta;
  vendor.lock: URLs + SHA-256 de los paquetes es→en / en→es 1.0. Modelos en vendor/models/argos/{es_en,en_es} (ignorado por git).
- Tests: test_subs_resync (retraso, deriva 4 % con ruido, corte de 12 s, sin coincidencias, formatos/codificaciones),
  test_subs_translate (segmentación, agrupación/reparto, store/zip sintético, traducción real es→en ≥5 palabras clave),
  test_subs_service (resync pending→done con desfase −2,5 s recuperado; traducción job→SRT inglés, caché, errores),
  test_mu_subs ampliado (resync desde el menú → pista "Resincronizado", traducción → pista "Traducción (en)", duales con
  `secondary-sub-text` y `sub-text`).
- Probar a mano:
  ```bash
  uv sync --extra translate
  bin/mpv-uos pelicula.mkv                 # alt+c (subtítulos IA) · alt+s carga un .srt externo → alt+x lo resincroniza
                                           # alt+i → Traducir la pista seleccionada a… → Inglés; luego Duales
  .venv/bin/python -m mpvd call subs.translate.models
  .venv/bin/python -m mpvd call subs.resync '{"path":"pelicula.mkv","srt":"pelicula.srt","language":"es"}'
  ```
#### H6 · Sincronía, traducción y duales — plan
- Verificado (mpv 0.41 `--list-options/--list-properties`): `secondary-sid` (no|auto|0-8190), `secondary-sub-pos` (0-150, 0 = arriba),
  `secondary-sub-delay`, `secondary-sub-visibility`, `secondary-sub-text`, `sub-delay`, `sub-speed`, `sub-pos`.
- Resync (`mpvd/subs/`): `formats.py` carga SRT/VTT/ASS (codificación con BOM/utf-8/cp1252) → cues; `resync.py` alinea las cues del
  externo con los segmentos Whisper de la tarea `asr` del archivo: similitud por Jaccard de palabras sin acentos dentro de una banda
  de ±120 s, cadena monótona de máximo peso (LIS ponderada), desfase por tramos = mediana de los pares por ventana + interpolación
  lineal entre ventanas (deriva). Servicio `subs.resync {path, srt, model?, language?}` → SRT corregido en caché + estadísticas
  (pares, desfase mediano, deriva); si no hay transcripción, lanza `asr` (precompute) y devuelve `pending` con el id de la tarea.
- Traducción: según docs/TRADUCCION.md (subagente): paquetes Argos (CTranslate2 + sentencepiece) sin argostranslate/stanza; servicio
  `subs.translate {path, srt|task, source, target}` con caché por (hash, artefacto, par, versión) → SRT traducido.
- Duales: mu-subs "Duales: original arriba + traducción abajo" = `secondary-sid` original (arriba, `secondary-sub-pos=0`... por
  defecto ya es 0) y `sid` traducción; menú Subtítulos IA → entradas "Resincronizar…", "Traducir…", "Duales".
#### H5 · Subtítulos IA en vivo — hecho (commit "H5: subtítulos IA en vivo")
- `tools/vendor_whisper.sh` copia whisper-cli/whisper-server + libs ggml/whisper + modelos desde el build de live-captions-linux
  (ADR-023); `vendor/whisper/VERSION`. `tools/bench_asr.sh` → docs/BENCHMARKS.md (i5-6200U, 3 hilos: base RTF 0,26–0,38, tiny 0,20,
  small-q8_0 ≈1,0; q5_1 más lentos que q8_0). Tablas de tier en `asr/models.py`: small/medium → base en vivo; large → small-q8_0.
- mpvd `asr/`: `audio.py` (WAV 16 kHz vía ffmpeg), `srt.py` (cues, fusión por trozos, SRT), `models.py` (catálogo, descarga bajo
  demanda con magia ggml, elección por tier), `engine.py` (whisper-cli por trozo, JSON, cerrojo global), `service.py` (tareas por
  archivo+modelo+idioma, plan de trozos de 20 s ordenado por distancia al cursor, `asr.seek` mueve el cursor, estado en caché de
  artefactos → reanudación instantánea, eventos push `asr` y `asr-model`). Métodos: asr.status/models/models.download/models.remove/
  start/precompute/seek/stop/segments. Solo archivos locales con duración (ADR-023).
- `mu-subs/main.lua`: menú "Subtítulos IA" (alt+i; botón CC): iniciar/detener (alt+c), idioma, modelo (descargar/borrar, progreso),
  activar automáticamente, pre-subtitular el siguiente de la lista, estado del motor. `sub-add` de la primera versión del SRT y
  `sub-reload` (≤1/s) en cada evento; `asr.seek` en seeks y cada 5 s; adopta desde caché la tarea pre-calculada del siguiente.
- mu-menu: entrada "Subtítulos IA (whisper)"; uosc.conf: `button:mu-subs`; input.conf + docs/ATAJOS.md; check.sh: paso whisper.
- Docs: docs/WHISPER.md (opciones reales, JSON, hallazgos: coste fijo del encoder de 30 s, FLAC vacío por miniaudio, tiny confunde
  idioma en `auto` con voz sintética), docs/BENCHMARKS.md, ADR-023/024, README, PLATAFORMAS.
- Tests: test_asr_engine (argv vs `--help` real, JSON, catálogo/tier, transcripción real de voz_es ≥3 palabras clave con base),
  test_asr_service (daemon: errores, tarea en vivo sobre la pista inglesa de voz_es_en.mkv, SRT sin solapes, reanudación desde caché
  y tras reiniciar mpvd, trozo bajo el cursor primero, precompute), test_mu_subs (mpv headless: pista externa añadida y seleccionada,
  `sub-text` muestra la cue tras un seek, precompute del siguiente adoptado al cambiar, menús root/idioma/modelos/estado, URL/idle
  rechazados con OSD). Se omiten si falta whisper (tools/vendor_whisper.sh).
- Latencia medida en tests (base, 3 hilos): primer trozo de 6 s listo en ≈4 s; trozo de 20 s en ≈5–6 s (RTF ≈0,3); mpv sin drops
  (headless). Pendiente de H5 ampliado: URLs/directos (grabar el audio desde la URL resuelta).
- Probar a mano:
  ```bash
  tools/vendor_whisper.sh && tools/bench_asr.sh          # binarios + tabla de RTF
  bin/mpv-uos tests/fixtures/media/voz_es_en.mkv          # alt+i → Iniciar (o alt+c); aparece la pista "Subtítulos IA (base · es)"
  .venv/bin/python -m mpvd call asr.status                # tareas, RTF, modelos; asr.models para descargar otros
  MPV_UOS_TEST_ASR_MODEL=tiny uv run pytest tests/test_asr_service.py tests/test_mu_subs.py -q
  ```
#### H5 · Subtítulos IA en vivo — plan
- Verificado en esta máquina (vendor/whisper/bin, libwhisper 1.9.3 de live-captions-linux): `whisper-cli --help` real
  (-m/-f/-t/-l/-oj/-of/-np/-tr/-bs/-bo/-nf/--prompt/--vad/--vad-model/-ml/-sow), JSON `transcription[].offsets{from,to}` en ms,
  `result.language`. El FLAC leído por miniaudio da vacío → siempre WAV vía ffmpeg (ya lo hace asr/audio.py). Coste fijo por
  llamada = encoder de 30 s (tiny 1.3 s, base 3.0 s, small-q5_1 12 s con 3 hilos, incluso con 1 s de audio): el modelo se carga
  rápido, así que whisper-server no aporta nada y se descarta (ADR-024); trozos de 20 s por defecto.
- mpv 0.41 (`--input-cmdlist`): `sub-add url [flags] [title] [lang]`, `sub-reload [id]`, `sub-remove [id]`. mu-subs localiza la
  pista por `external-filename` en `track-list` (el id puede cambiar al recargar) y recarga como mucho 1 vez/s.
- Pasos: tools/vendor_whisper.sh (copia binarios+libs+modelos desde live-captions-linux o $WHISPER_BUILD) → tools/bench_asr.sh →
  docs/BENCHMARKS.md (RTF por modelo/hilos/VAD) → ajustar tablas de tier en asr/models.py → repaso asr/service.py (chunk 20 s,
  progreso de descarga de modelos por push) → mu-subs/main.lua (menú "Subtítulos IA": iniciar/parar, idioma, modelo, descargar
  modelo, pre-subtitular el siguiente; sub-add/sub-reload; asr.seek en seek y cada 5 s; botón uosc con progreso; alt+i / alt+c)
  → tests (engine con tiny/base sobre voz_es/voz_en por palabras clave; servicio asr.* vía daemon con reanudación desde caché;
  mu-subs headless: pista externa añadida, cues correctas, precompute del siguiente) → docs (WHISPER.md, ATAJOS.md, ADR-023/024,
  PLATAFORMAS) → check.sh → commit.
### Iteración 1 · 2026-09-28
#### H0 · Cimientos — plan
- Entorno verificado: mpv 0.41.0 (Lua OK; vo gpu-next; hwdec vulkan/vaapi/nvdec), uv 0.12.14 con CPython 3.12.14 disponible,
  ffmpeg 8.0.1 (libx264, aac, libopus, flac, libmp3lame), espeak-ng 1.52 (voces es, en-us), luajit 2.1 + luacheck, yt-dlp 2026.08.19,
  node, jq, sqlite3, fpcalc, vulkaninfo/vainfo. Hardware: 4 núcleos, 23 GB RAM, sin GPU dedicada. Hostname pc-latitude5480.
- Terceros: uosc 5.13.0 (release 2026-08-03) y thumbfast master 0f711de (2026-06-28); hashes SHA-256 fijados en vendor.lock.
- Pasos: pyproject + uv (paquete mpvd, cliente IPC) → bin/mpv-uos → mpv-config (mpv.conf, input.conf, uosc, thumbfast, mu-core esqueleto)
  → tools/make_test_media.sh → tests pytest headless + tools/check.sh → README y docs/PLATAFORMAS.md.
#### H0 · Cimientos — hecho (commit "H0: cimientos")
- `pyproject.toml` (paquete `mpvd`, build uv_build, pytest + pytest-timeout), `.python-version`=3.12, `.venv` con `uv sync`.
- `mpvd/mpvipc.py`: cliente asyncio del JSON IPC de mpv (command/get/set, cola de eventos, wait_event, wait_property).
- `bin/mpv-uos`: lanza el mpv del sistema con `--config-dir=<proyecto>/mpv-config` y `--input-ipc-server=$XDG_RUNTIME_DIR/mpv-uos/mpv-<pid>.sock`;
  exporta `MPV_UOS_ROOT` y `MPV_UOS_SOCKET`.
- `mpv-config/`: mpv.conf (gpu-next, auto-safe, save-position-on-quit, osc=no, osd-bar=no…; cada clave validada por test), input.conf con
  menú uosc (`#!`), uosc 5.13.0 + thumbfast 0f711de vendorizados (`vendor.lock`, `tools/vendor.sh`, ziggy fuera de git), `mu-core.lua` esqueleto
  que publica `user-data/mu/core` y detecta uosc.
- `tools/make_test_media.sh`: video30.mkv, chapters.mkv (3 capítulos), voz_es/voz_en.flac (espeak-ng, 16 kHz mono, JSON con frases y
  palabras clave), voz_es_en.mkv (2 pistas de audio spa/eng), manifest.json.
- `tools/check.sh`: entorno → vendor → medios → luacheck → shell lint → pytest (sin red) → pytest -m network si hay conectividad.
- Tests (16): launcher, opciones de mpv.conf contra `--list-options`, input.conf sin errores, vendor.lock ↔ uosc instalado, smoke headless
  (uosc/thumbfast/mu-core cargan sin errores Lua, uosc registra bindings, reproduce capítulos y pistas), cliente IPC, medios ↔ manifest.
- Probar a mano:
  ```bash
  tools/check.sh                                   # todo en verde
  bin/mpv-uos tests/fixtures/media/chapters.mkv    # ventana con uosc en español; botón derecho = menú
  bin/mpv-uos --vo=null --ao=null --idle=yes --input-ipc-server=tmp/a.sock &   # headless
  echo '{"command":["get_property","user-data/mu/core"]}' | socat - UNIX-CONNECT:tmp/a.sock   # o con python: mpvd.mpvipc
  ```
- Pendiente de H0: registro en Notion (delegado a subagente en esta iteración).
#### H1 · mpvd núcleo — plan
- Transporte: mpv Lua no tiene sockets, así que mu-core NO mantiene conexión; lanza `python -m mpvd ensure --attach <ipc>` (subproceso corto,
  asíncrono) que arranca el daemon si no responde y registra la sesión. Después es **mpvd quien se conecta al IPC de mpv** (cliente JSON IPC)
  y desde ahí: recibe peticiones JSON-RPC que los scripts envían con `script-message mu-rpc <json> [script-destino]` (evento `client-message`),
  responde con `script-message-to <script> mu-reply <json>`, observa propiedades (frame-drop-count, pause) para el guardián y detecta el cierre.
- Módulos: config (rutas runtime/caché dev vs XDG), rpc (JSON-RPC 2.0 con batch y errores estándar), server (socket Unix, pidfile, idle-timeout),
  sessions (una por mpv), hashing (OpenSubtitles + blake2b de tamaño+64 KB inicio/fin), cache (SQLite + blobs, claves
  (hash, artefacto, modelo, versión, parámetros)), jobs (cola con prioridades URGENT/INTERACTIVE/PRECOMPUTE/INDEX, cancelación, progreso),
  guardian (tasa de frames perdidos → frena trabajos pesados), client + CLI (serve/ensure/call/status/stop).
- mu-core.lua: opciones (script-opts mu-core-*), ensure con reintentos/backoff, `mu-hello`/`mu-reply`, API `rpc(method, params, cb)`,
  watchdog (ping periódico), `mu-call` para pruebas que guarda en `user-data/mu/last_reply`, estado en `user-data/mu/core`.
- Tests: unit (rpc, hashing, cache, jobs, guardian, server in-process) + integración (mpv headless → mu-core arranca mpvd → sesión
  registrada → ping ida y vuelta → hash de archivo → shutdown del daemon → reconexión por watchdog → cierre limpio).
- Notion: ficha creada (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) y Bitácora `mpv-uos|2026-09-28|avance|h0-cimientos`.
  Slug añadido a CLAUDE.md. Detalles pendientes para Ser en NEEDS_HUMAN.md (Stack, Repo, marcador ~/.cache/notion-reg).
#### H1 · mpvd núcleo — hecho (commit "H1: mpvd núcleo")
- `mpvd/`: `rpc.py` (JSON-RPC 2.0, batch, códigos estándar), `server.py` (socket Unix 0600, pidfile, socket viejo limpiado, idle-timeout),
  `sessions.py` (una conexión IPC por mpv, hello, observe_property, mu-rpc/mu-reply), `methods.py` (ping, version, capabilities,
  shutdown, sessions.*, jobs.*, guardian.*, file.hash, cache.*), `jobs.py` (cola con prioridades urgent/interactive/precompute/index,
  cancelación, progreso, historial), `guardian.py` (tasa de frame-drop-count > 2/s → frena trabajos `heavy` 10 s), `hashing.py`,
  `cache.py` (SQLite WAL + blobs, get/put/invalidate/stats/prune LRU), `hardware.py` (tier small/medium/large), `client.py`,
  `__main__.py` (serve / ensure / call / status / stop), `config.py` (rutas dev .cache vs XDG).
- `mu-core.lua` 0.2.0: opciones `mu-core-*`, ensure con backoff, `mu-hello`, `rpc()` con timeouts, watchdog, `mu-call` (guarda en
  `user-data/mu/last_reply`), estado en `user-data/mu/core`.
- Tests (59 en total): rpc, hashing (referencia independiente), cache, jobs+guardian, server in-process, e integración real:
  mpv headless → mu-core arranca mpvd → sesión registrada con pid → ping/file.hash/sessions.current ida y vuelta → observa `path` →
  trabajo cancelado al cerrar mpv → watchdog reconecta tras `shutdown` → CLI `ensure` idempotente.
- Probar a mano:
  ```bash
  bin/mpv-uos tests/fixtures/media/video30.mkv           # mu-core arranca mpvd solo
  .venv/bin/python -m mpvd status                        # sesiones, trabajos, guardián
  .venv/bin/python -m mpvd call capabilities             # métodos disponibles
  .venv/bin/python -m mpvd call jobs.sleep '{"seconds":3,"heavy":true}' && .venv/bin/python -m mpvd call jobs.list
  .venv/bin/python -m mpvd call file.hash '{"path":"tests/fixtures/media/video30.mkv"}'
  .venv/bin/python -m mpvd stop                          # mu-core lo relanza en ≤30 s (watchdog)
  tail -f .cache/mpvd.log
  ```
- Dentro de mpv (consola `): `script-message-to mu_core mu-call ping` y leer `user-data/mu/last_reply`.
#### H2 · TV y radio — plan
- Verificado en mpv 0.41: `http-header-fields` (lista), `user-agent`, `referrer`, `tls-verify`, `stream-lavf-o`, `stream-record=<archivo>`,
  `loadfile <url> replace -1 {opciones por archivo}` (índice -1 obligatorio desde 0.38), `metadata/by-key/<k>` para el título ICY.
- Fuentes reales y fixtures: subagente descarga TDTChannels (tv/radio/tvradio), iptv-org (index + índices por país/categoría/idioma + API
  JSON) y Radio Browser; deja docs/FUENTES_IPTV.md y tests/fixtures/iptv/. Menu API de uosc 5.13 verificada en docs/UOSC_API.md.
- mpvd: `net.py` (caché HTTP con ETag/Last-Modified, TTL, offline) → `iptv/m3u.py` (parser tolerante) → `iptv/model.py` (canal
  normalizado + cabeceras → opciones mpv) → `iptv/sources.py` (fuentes configurables, refresco) → `iptv/index.py` (búsqueda sin acentos)
  → `iptv/radiobrowser.py` → `iptv/store.py` (favoritos/recientes en data_dir) → métodos RPC `iptv.*` → salud opcional (ffprobe, INDEX).
- mu-iptv.lua: menú uosc "TV y radio" (España TV/radio, Mundo por país→categoría, Radio mundial, Favoritos, Recientes, Buscar), botón en
  controles, reproducción con opciones por archivo, zapping ±1 en el grupo, OSD con nombre, ICY en pantalla, grabación (stream-record).
#### H2 · TV y radio — hecho (commits "H2 (parte 1)" y "H2 (parte 2)")
- Backend (parte 1): `mpvd/net.py` caché HTTP (ETag/Last-Modified, TTL 12 h, offline con la última copia), `iptv/m3u.py` parser tolerante
  (tvg-*, group-title, url-tvg, #EXTVLCOPT, #KODIPROP, comillas escapadas, HLS vs lista), `iptv/model.py` canal normalizado + cabeceras →
  opciones de mpv por archivo + sufijos iptv-org `(720p)`/`[Geo-blocked]`/`[Not 24/7]` a campos, `iptv/index.py` búsqueda sin acentos,
  `iptv/store.py` favoritos/recientes/listas de usuario/salud (SQLite), `iptv/sources.py` fuentes (TDTChannels tv+radio, iptv-org) y
  `iptv/service.py` métodos `iptv.*` (sources, refresh, facets, countries, channels, search, channel, play, zap, favorites.*, recents.*,
  sources.add/remove, health.check/status).
- Parte 2 (esta sesión, interactiva): `iptv/radiobrowser.py` (Radio Browser: países, tags, por país, búsqueda, top, contador de clics) con
  métodos `radio.*`; `mpv-config/scripts/mu-iptv/main.lua` + `script-modules/mu/{rpc,uosc}.lua`: menú uosc "TV y radio" (España TV/Radio,
  Mundo país→categoría con nombres y banderas de la API de iptv-org, Radio mundial, Favoritos, Recientes, Mis listas, Buscar en paleta),
  acciones por ítem (favorito, copiar URL, quitar lista), botón `mu-tv` en la barra de uosc, reproducción con opciones por archivo,
  zapping ±1 dentro del grupo, OSD con nombre y título ICY, grabación `stream-record` a `~~desktop/MPV-UOS`, comprobación de salud en
  segundo plano desde la lista. Teclas en input.conf: alt+t menú, alt+f buscar, alt+UP/DOWN zapping, alt+r grabar.
- Correcciones de esta sesión: el estado de navegación sigue a `user-data/uosc/menu/type` leído en nativo (la forma string de una
  sub-clave de user-data es JSON con comillas, por lo que las comparaciones con el tipo nunca acertaban y cada `show()` reabría el menú)
  y ya no depende del evento `close`, que uosc emite desde su hilo en mitad de un reemplazo; los tests aíslan `MPV_UOS_DATA_DIR` por
  ejecución (antes compartían `.cache/data` y un favorito de una pasada se desmarcaba en la siguiente); `wait_property` tolera
  `property not found` en user-data; límite de línea JSON de IPC/JSON-RPC a 32 MiB;
  filas compactas con `geo_blocked`/`not_24_7`; test de red de iptv-org usa el país del probador (MPV_UOS_TEST_COUNTRY, por defecto es)
  y registra como xfail si ningún stream arranca.
- Tests: parser con fixtures reales recortadas (tests/fixtures/iptv), servicio con servidor HTTP local, Radio Browser simulado,
  integración headless mu-iptv (menús, reproducción, zapping, favoritos, grabación real de un directo por HTTP, búsqueda, mundo, radio,
  Mis listas + salud) y @network (descarga de listas reales, directorio Radio Browser, reproduce 3+3+3 canales reales).
- Probar a mano:
  ```bash
  bin/mpv-uos --idle=yes            # alt+t → TV y radio; alt+f busca; Tab sobre un canal → ★ / copiar URL
  .venv/bin/python -m mpvd call iptv.refresh                      # descarga/actualiza listas (caché 12 h)
  .venv/bin/python -m mpvd call iptv.search '{"q":"antena 3","compact":true}'
  .venv/bin/python -m mpvd call iptv.countries '{"source":"iptv_org"}'
  .venv/bin/python -m mpvd call radio.stations '{"country":"es","limit":5,"compact":true}'
  .venv/bin/python -m mpvd call iptv.sources.add '{"name":"Mi lista","url":"https://ejemplo/lista.m3u"}'
  MPV_UOS_TEST_COUNTRY=es uv run pytest -m network tests/test_network_iptv.py -s   # reproduce canales reales
  ```
- Runner nocturno: `tools/nocturno.sh` espera al reset del cupo de sesión de Claude (hora del mensaje o 30 min) sin contarlo como fallo,
  tope de 3 h por iteración, effort `high` (.runner.env) y `DEADLINE=07:30`. Lanzamiento: `tmux kill-session -t mpvuos;
  tmux new-session -d -s mpvuos "cd <proyecto> && systemd-inhibit --what=sleep:idle bash tools/nocturno.sh; exec bash"`.
#### H5 · Subtítulos IA en vivo — plan
- Notion al día (2026-09-29): avances H3/H4, decisiones ADR-019/021 y Próximo paso en la ficha (vía subagente).
- Verificación (subagente → docs/WHISPER.md): whisper.cpp de ~/proyectos/live-captions-linux (copiado a vendor/whisper si sirve,
  si no compilado desde un tag estable), `--help` real de whisper-cli/whisper-server, VAD Silero, formato JSON, RTF por modelo.
- Ya hecho sin depender del modelo: `mpvd/asr/audio.py` (ventana WAV 16 kHz mono con ffmpeg, verificado) y `mpvd/asr/srt.py`
  (segmentos, SRT incremental, fusión por trozos con recorte de solapes) + tests.
- Siguiente: `asr/engine.py` (whisper-server persistente con fallback a whisper-cli), `asr/service.py` (`asr.*`: start/seek/stop/
  status/models; trozos de 10 s con solape, prioridad URGENT para el trozo por delante de time-pos, PRECOMPUTE para el resto y
  el siguiente de la playlist; SRT en caché por hash; eventos push `asr`), `mu-subs.lua` (menú "Subtítulos IA": iniciar/parar,
  idioma, modelo; `sub-add` la primera vez y `sub-reload` en cada actualización; `asr.seek` en seeks; progreso en uosc),
  tools/bench_asr.sh → docs/BENCHMARKS.md, tests con el modelo tiny sobre voz_es/voz_en (palabras clave del manifest).
- Iteración 2 (2026-09-29): binarios de whisper.cpp ya copiados en vendor/whisper (de live-captions-linux, libwhisper 1.9.3,
  funcionan con LD_LIBRARY_PATH); la compilación de vendor/whisper-src (v1.9.4) murió por tiempo → se descarta (ADR-023).
  Diseño: un solo trabajo `heavy` por archivo que recorre un plan de trozos ordenado por distancia a time-pos (seek = mover el
  cursor, sin cancelar trabajos); semáforo global de 1 proceso whisper; SRT estable en .cache/asr/<clave>/ + estado en la caché
  de artefactos (reanudable); modelos bajo demanda desde Hugging Face con verificación de magia ggml; eventos push a mu_subs.
#### H4 · UX base — hecho (commit "H4: UX base")
- mpvd `watch.py` (servicio `watch.*`: get/update/recents/search/remove/clear; clave por contenido; historial de 500 entradas).
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m, botón `mu-menu` en la barra; "Continuar viendo" inline),
  vista Recientes (alt+h; Tab olvida; borrar historial), paleta global (alt+p; comandos de `input-bindings` + curados, canales,
  recientes, acciones de mpvd), continuar viendo (seek al cargar si procede; guarda cada 15 s/pausa/seek/fin; directos excluidos),
  pantalla de inicio en idle. mu-iptv: `mu-iptv-play <id>` y `current_url`. uosc.conf: botones `mu-menu` y `mu-ytdl`.
- docs/ATAJOS.md completo + tests/test_atajos.py (cada tecla de input.conf documentada y cada script-binding existente).
- Tests: test_watch.py (store, clave estable al renombrar, métodos), test_mu_menu.py (reanudación tras renombrar, no reanuda lo
  terminado, menú raíz con recientes, paleta con comandos/canales/recientes/acciones, reproducir canal desde la paleta, pantalla
  de inicio). conftest: `start_screen` en start_mpv (desactivada por defecto).
- Probar a mano:
  ```bash
  bin/mpv-uos                                  # pantalla de inicio (recientes + accesos); alt+p paleta; alt+m menú
  bin/mpv-uos tests/fixtures/media/video30.mkv # avanza a 0:25, cierra con q; cópialo con otro nombre y ábrelo: reanuda en 0:25
  .venv/bin/python -m mpvd call watch.recents
  .venv/bin/python -m mpvd call watch.search '{"q":"video"}'
  ```
#### H4 · UX base — plan
- Verificado: `input-bindings` (mpv 0.41) devuelve `{key, cmd, comment, section, priority, is_weak}`; los comentarios `#!` de
  input.conf llegan como `comment = "! Título > Sub"` → base de la paleta de comandos. `idle-active` + `playlist-count` para la
  pantalla de inicio. `--script-opts=` posterior sustituye la lista entera (usar `--script-opts-append` en tests).
- mpvd `watch.py` (servicio `watch.*`): SQLite en data_dir con clave por contenido (`file.hash` para archivos, `url:` para URLs):
  get/update/recents/search/remove/clear; "terminado" si posición ≥ duración-30 s o ≥95 %.
- `mu-menu/main.lua`: menú raíz "MPV-UOS" (MBTN_RIGHT/MENU/alt+m; botón `mu-menu` primero en la barra; incluye "Menú completo"
  = uosc/menu), paleta global (alt+p: comandos de input-bindings + lista curada, canales vía iptv.search, recientes vía
  watch.search, acciones de mpvd), continuar viendo por hash (seek en file-loaded si mpv no reanudó ya; posición cada 15 s,
  en pausa/seek/end-file), pantalla de inicio en idle (opción `mu-menu-start_screen`, desactivada en tests salvo el suyo).
- mu-iptv gana `script-message mu-iptv-play <id>` para la paleta. uosc.conf: `button:mu-menu` y `button:mu-ytdl` en controls.
- docs/ATAJOS.md + test que comprueba que todas las teclas de input.conf están documentadas.
#### H3 · yt-dlp avanzado — hecho (commit "H3: yt-dlp avanzado")
- Verificación real: docs/YTDLP.md (release 2026.08.19, assets y SHA, runtime JS/EJS, `--help` completo, `-J`, progreso JSON,
  fixtures) y docs/MPV_YTDL.md (ytdl_hook embebido en mpv 0.41: opciones, lectura en caliente de script-opts, `user-data/mpv/ytdl/*`,
  sintaxis `loadfile … replace -1 {…}`, pruebas headless).
- Vendorizado: `vendor.lock` fija `yt-dlp` 2026.08.19 (zipimport, SHA-256) y las sumas de deno v2.9.7 por plataforma;
  `tools/vendor.sh` instala vendor/bin/yt-dlp (no pisa una versión más nueva instalada por mpvd) y deno solo si no hay runtime JS.
- mpvd `ytdl/`: `binary.py` (resolución env → vendor → PATH, runtime JS deno/node≥22, ffmpeg, updater diario verificado con
  SHA2-256SUMS), `info.py` (filas de formato con etiqueta/hint, agrupación combinado/vídeo/audio, resumen, playlists planas),
  `presets.py` (DownloadSpec → argv exacto; 14 presets; política de contenedor ADR-019), `downloads.py` (cola sobre JobQueue,
  progreso `MU_PROGRESS`/`MU_PP`/`MU_DONE`, cancelar/repetir/quitar, historial y ajustes persistentes, carpetas XDG),
  `service.py` (métodos `ytdl.status/hook/update.check/update.apply/info/playlist/presets/download/downloads.*/settings.*`,
  caché de `-J` por URL con semilla del hook, eventos push a todas las sesiones). `sessions.push_event` genérico.
- `mu-ytdl/main.lua`: fija `ytdl_hook-ytdl_path` y `ytdl-raw-options js-runtimes` en caliente; conmutador vídeo/solo audio
  (alt+a) en la misma posición; menú Calidad (alt+q) con cambio en caliente y acción "descargar este formato"; menú Descargar
  (alt+d) con presets y opciones conmutables; panel Descargas (alt+l) en vivo desde `mu-event`; Estado de yt-dlp con
  actualización manual; botón ⬇ en la barra con contador; `user-data/mu/ytdl` con estado e items del menú para tests.
- Tests: presets (argv exacto), info (fixtures reales de YouTube/archive.org/playlists), binario+updater (servidor HTTP local,
  SHA incorrecto rechazado), descargas y métodos con un yt-dlp falso (`tests/fixtures/ytdlp/fake_ytdlp.py`), integración headless
  mu-ytdl (ytdl_hook real ejecutando el fake, medios por HTTP, conmutador, calidad, descarga, panel) y @network (info real,
  comprobación en GitHub, descargas reales 360p/mp3 128k/mkv verificadas con ffprobe). conftest aísla watch_later/resume.
- Probar a mano:
  ```bash
  bin/mpv-uos https://www.youtube.com/watch?v=aqz-KE-bpKQ   # alt+q calidad · alt+a solo audio · alt+d descargar · alt+l descargas
  bin/mpv-uos https://archive.org/details/Countdow1960
  .venv/bin/python -m mpvd call ytdl.status
  .venv/bin/python -m mpvd call ytdl.update.check '{"force":true}'
  .venv/bin/python -m mpvd call ytdl.download '{"url":"https://archive.org/details/Countdow1960","preset":"video_360"}'
  .venv/bin/python -m mpvd call ytdl.downloads.list
  uv run pytest -m network tests/test_network_ytdl.py -s
  ```
- Test intermitente conocido: `test_mu_iptv.py::test_menus_play_zap_favorites_record_and_search` puede agotar el tiempo de
  espera del directo (ffmpeg -re) con la máquina cargada; se subió a 90 s. Pasa aislado.
#### H3 · yt-dlp avanzado — plan (original)
- Verificación previa (subagentes): docs/YTDLP.md (última release, assets, runtime JS para YouTube, opciones reales de `--help`,
  formato `-J`, `--progress-template`) y docs/MPV_YTDL.md (ytdl_hook de mpv 0.41: script-opts, `ytdl_path`, `all_formats`,
  `loadfile ... replace -1 {ytdl-format=…,start=…}`, `vid=no` + `audio-display`). Fixtures reales de `-J` en tests/fixtures/ytdlp/.
- Vendorizado: vendor.lock gana `YTDLP_VERSION/URL/SHA256` (asset `yt-dlp` zipimport, corre con el Python del .venv); tools/vendor.sh lo
  instala en vendor/bin/yt-dlp (+ envoltorio ejecutable). mpvd comprueba una vez al día (`ytdl.update.check`) la última release en
  GitHub (caché HTTP) y la descarga verificando el SHA2-256SUMS oficial; nunca en el hilo de mpv. Runtime JS (deno) vendorizado en
  vendor/bin si YouTube lo exige (verificar en docs/YTDLP.md).
- ytdl_hook: `script-opts/ytdl_hook.conf` con `ytdl_path=<ruta absoluta>` no vale (la config debe ser portable) → mu-ytdl fija
  `ytdl_hook-ytdl_path` en caliente vía `script-opts` al arrancar apuntando a vendor/bin (relativo a MPV_UOS_ROOT).
- mpvd `ytdl/`: `binary.py` (localizar/actualizar binario), `info.py` (`-J` con caché por URL, agrupación de formatos), `presets.py`
  (argumentos de cada preset: exacto/combinación/audio original/convertido/opciones extra), `downloads.py` (cola con progreso
  `--newline --progress-template`, cancelar, reintentar, carpetas XDG y plantilla), `service.py` (métodos `ytdl.*`).
- mu-ytdl.lua: conmutador vídeo/solo audio (tecla + menú, `loadfile … replace -1 {ytdl-format=…,start=<pos>}` conservando pausa,
  velocidad y volumen), menú "Calidad" (todos los formatos agrupados) y "Descargar" (presets), panel "Descargas" con progreso
  (eventos push `mu-event` desde mpvd), botón en la barra de uosc y teclas en input.conf.
- Tests: unit (presets → argv exacto, parseo de `-J` desde fixtures, parser de progreso, comprobación de actualización con servidor
  local), integración headless (mu-ytdl con un yt-dlp falso que devuelve el JSON de fixture y "descarga" un archivo local con
  progreso), @network (descarga real corta en 2 presets verificada con ffprobe).

### Sesión interactiva · 2026-09-30 · H14 (pruebas de Ser)
Ser probó la app y reportó 9 problemas; se diagnosticaron en 6 frentes (tmp/diag-*) y se arreglaron (núcleo en la sesión; yt-dlp,
intro, subtítulos, preferencias y TV en agentes con worktree, fusionados). `tools/check.sh`: lint 0 avisos, 292 tests sin red y 8 con
red en verde (un test real de YouTube falló una vez por la red y pasó al repetir).
- **Núcleo**: el reproductor nunca se cierra al fallar una carga y explica la causa en español; vuelve a la pantalla de inicio;
  socket por instancia y sesiones de mpvd por PID; datos de usuario en `~/.local/share/mpv-uos` (migrados); watch_later solo por
  archivo (arregla audio perdido al cambiar de archivo y La 1 fijada a 360p); hwdec=vaapi primero; desentrelazado con copia.
- **Interfaz**: botón play/pausa; teclas de mpv restauradas (`v`, `ctrl+alt+v`) y agrupadas en «Más opciones»; `ctrl+v` abre el
  portapapeles; paleta más certera; «Abrir URL» (`ctrl+u`) y «Buscar en YouTube» (`ctrl+f`).
- **yt-dlp**: runtime JS desde el primer vídeo, H.264 primero, preset mp4 real, búsqueda en YouTube; recuerda «solo audio».
- **Subtítulos**: `alt+S` guarda en SRT (IA, traducción, resync, pista interna); motor de traducción «Calidad» (OPUS-MT, ya en
  vendor/models/opus-mt para es/ca↔en); pre-subtitulado con small-q8_0 y trozos de 28,5 s con contexto.
- **Saltar intro**: episodios en carpetas hermanas, avisos visibles, versiones del mismo vídeo descartadas, marcado manual,
  salto automático con cuenta atrás, temporada completa; nada se escribe junto a los vídeos salvo «Exportar».
- **Preferencias**: se recuerdan volumen, velocidad, subtítulos, imagen, idiomas, filtros, opciones de cada módulo…;
  Preferencias › Restablecer.
- **TV**: directos sin watch_later, HLS estable, User-Agent de navegador, calidad visible, copias con anuncios detrás del oficial y
  cambio automático de fuente, países y categorías en español. Lo que queda (720p de RTVE, 25 fps, bitrate bajo) es de la fuente.
- **Mando**: el QR avisa del cortafuegos con la orden exacta. **Instalador**: carpeta movida, MIME completos, restaura reproductores.
- Probar a mano:
  ```bash
  mpv-uos                                   # inicio · ctrl+u abrir URL · ctrl+f buscar en YouTube · alt+t TV
  mpv-uos 'https://www.youtube.com/watch?v=aqz-KE-bpKQ'   # alt+a solo audio (se recuerda) · alt+q calidad · alt+d descargar
  mpv-uos ~/Descargas/…/Don\ Matteo\ 1x06…mp4   # «Analizando…», luego alt+k salta intro · alt+j menú
  mpv-uos pelicula.mkv                      # alt+i subtítulos IA · Traducir (Calidad) · alt+S guardar SRT
  ```
