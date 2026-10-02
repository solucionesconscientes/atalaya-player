# Lo que falta, lo que nadie tiene, y cómo simplificar (repaso del 2026-10-02)

Pedido por Ser: «piensa todas las funcionalidades que no se nos han ocurrido, presentes en otros reproductores, y
las que aún no están en otros reproductores», y «todas las mejoras que puedas en UI y simplicidad».

Antes de proponer nada repasé el BACKLOG entero (58 hitos). Aquí **no** hay nada que ya esté hecho: ni resumen e
índice, ni búsqueda por significado, ni saltar intro, ni subtítulos con IA, ni salas, ni mando del móvil, ni
grabación programada con despertador, ni tramos, ni modo salón, ni suscripciones. Cada propuesta lleva **cuánto
cuesta** y **sobre qué se apoya**, porque una idea sin eso no es una decisión, es una lista de deseos.

---

## 1. Lo que tienen otros y aquí falta

### 1.1 Que sea lo mismo empezar en el portátil y acabar en la tele · **alto valor, coste medio**
Plex, Jellyfin y Netflix lo dan por hecho: dejas algo a medias en un sitio y sigue en otro. Aquí la posición se
guarda **por equipo** (`watch_later`). Ya existen las dos piezas difíciles —mpvd y el canal de eventos de las
salas—, así que falta decidir dónde vive la verdad y poco más. *Apoyo: mpvd, `watch_later`, salas.*

### 1.2 Volumen parejo entre canciones y entre vídeos · **alto valor, coste bajo**
Es la queja universal de cualquier lista: una canción revienta y la siguiente no se oye. mpv trae ReplayGain
(`--replaygain=track|album`) y filtros de normalización; es cuestión de ponerlo en *Imagen y sonido* con tres
opciones honestas (por pista, por disco, apagado). *Apoyo: mu-av.*

### 1.3 Un temporizador para dormirse, para todo · **valor medio, coste bajo**
Existe… **solo en audiolibros** (`mu-books`). Es de las cosas más usadas en cualquier aparato de salón y debería
estar en todo: «para en 30 min», «para al final de esto», y con la opción de **suspender o apagar**, que ya saben
hacer `power.after` y los seguros de H40. *Apoyo: mu-books, power.*

### 1.4 Listas guardadas de verdad · **alto valor, coste medio**
Hoy hay *una* lista (la de mpv) y se pierde al cerrar. Para lo que pediste —«programar canciones o playlist»—
hacen falta listas con nombre: crear, guardar, cargar, añadir «a continuación». Es lo que convierte la música en
algo que se puede programar. *Apoyo: mu-music, mu-menu, la lista de mpv.*

### 1.5 Capítulos de YouTube y marcadores del propio vídeo · **valor medio, coste bajo**
yt-dlp trae los capítulos que el autor escribió en la descripción; aprovecharlos sale casi gratis y es lo primero
que busca quien ve charlas largas. *Apoyo: mu-ytdl, mu-marks (ya dibuja en la línea de tiempo).*

### 1.6 HDR a SDR decente · **valor medio, coste medio**
Cada vez más descargas vienen en HDR y en un portátil sin pantalla HDR se ven lavadas. mpv sabe hacer el mapeo de
tonos; falta elegirlo bien y ofrecerlo donde se note. *Apoyo: mu-av, detección de hardware.*

### 1.7 Perfiles por persona · **valor medio, coste medio**
Un portátil de casa lo usan varios. Hoy «Continuar viendo», las notas y los favoritos son de todos. *Apoyo:
mu-prefs, biblioteca.*

### 1.8 Mando de salón de verdad (HDMI-CEC) · **valor bajo aquí, coste alto**
Manejar el reproductor con el mando del televisor. Depende del hardware y no hay forma de probarlo aquí: lo dejo
anotado, no propuesto.

---

## 2. Lo que no tiene nadie, y aquí sale casi solo

Esto es lo interesante: hay **tres cosas** que nadie hace bien y que este reproductor ya tiene casi montadas,
porque junta transcripción, significado y corte de vídeo en el mismo sitio.

### 2.1 «Ponme esta charla de una hora en quince minutos» · **el que yo haría primero**
Ya hay transcripción (Whisper), capítulos por tema (búsqueda semántica) y, desde H52, **cortar y pegar tramos en
una sola pasada**. Juntarlos da algo que no existe en ningún reproductor: pides una duración y el programa elige
los tramos que valen la pena y te deja el vídeo montado. No es un resumen escrito —eso ya lo hay—, es **el vídeo
acortado**, que es otra cosa y se puede ver con alguien.
*Apoyo: todo hecho menos la decisión de qué tramos elegir.*

### 2.2 «Enséñame solo donde hablan de X» · **mismo motor, otra pregunta**
La búsqueda por significado ya encuentra los minutos; lo que falta es que el resultado sea **reproducible como una
sola cosa** en vez de una lista de saltos. Para una clase, un partido o una reunión grabada, eso cambia el uso.

### 2.3 Clips que empiezan y acaban donde acaba la frase · **coste bajo, se nota mucho**
Al cortar un trozo para mandárselo a alguien, el corte cae a mitad de palabra. Con la transcripción se conocen los
límites de cada frase: el tramo se ajusta solo al principio y al final de lo que se está diciendo. Pequeño, barato
y de esas cosas que, una vez las tienes, no entiendes que falten.

### 2.4 Doblaje hablado de lo que está en otro idioma · **coste alto, valor alto**
Ya se transcribe y se traduce; falta la voz. Es caro en CPU para un portátil de cuatro núcleos y habría que
medirlo antes de prometerlo, pero es la diferencia entre leer subtítulos y **entender** un vídeo en alemán.

### 2.5 «No me cuentes el final» · **coste mínimo, cariño máximo**
Un interruptor que esconde el tiempo restante y la barra de progreso. En un partido o en una película de misterio,
ver «quedan 4 minutos» te acaba de contar el final. No lo hace casi nadie y cuesta una tarde.

### 2.6 Despertador con plan B
Lo que acabamos de hacer (programar que suene algo en una franja) se vuelve de verdad fiable si admite una
alternativa: «despiértame con la radio y, si el canal no emite o no hay internet, con esta lista».

---

## 3. Interfaz y simplicidad

Tres cosas, por orden de lo que más molesta.

### 3.1 Lo que pasa por detrás tiene que verse · **la lección de esta prueba**
«Guardar los tramos no hace nada» era **falso**: los archivos se creaban. Pero iban a una carpeta que nadie ha
visto y, al unir, se recodificaba en silencio. Si algo tarda, el programa tiene que decir **qué está haciendo,
dónde va a quedar y cuánto le falta**, y sin que haya que ir a buscarlo. Lo suyo es un indicador discreto en la
barra mientras hay trabajo en marcha, que lleve a Tareas de un clic. Es la mejora de interfaz con más retorno.

### 3.2 Cuando algo no se puede hacer, decirlo ANTES
El mismo caso: con la TV puesta, «guardar tramos» no podía funcionar y solo se sabía **después** de pulsar. La
regla que conviene fijar: *una opción que no puede funcionar ahora no se ofrece como si pudiera* — se queda, pero
apagada y diciendo por qué, y señalando lo que sí sirve. Ya está aplicado en tramos y en los mandos del invitado;
falta repasar el resto con ese criterio.

### 3.3 La barra y el menú
- La barra ha crecido hasta **once sitios**. El límite sano es ocho a la vez: la regla de `docs/INTERFAZ.md` ya lo
  permite (`hide`), pero hay que usarla más — *grabar* solo con algo que grabar, *tramos* y *nota* solo con
  duración.
- **El modo sencillo no encoge la barra** (H53): dice que lo hace y no lo hace.
- Las tres cosas que se hacen *con* el vídeo —tramos, notas, grabar— están en tres sitios distintos del menú.
  Juntarlas bajo una sola entrada haría el menú más corto y más fácil de explicar.
- Al pedir una hora o un texto se escribe en una paleta de búsqueda, que no se parece a escribir. Funciona, pero
  es lo que más desconcierta de todo lo que hay.

---

## 4. Qué haría yo, en este orden

1. **Que se vea lo que pasa por detrás** (3.1) y **avisar antes, no después** (3.2). Barato y arregla la queja
   real de esta prueba.
2. **Volumen parejo** (1.2) y **temporizador para dormirse en todo** (1.3). Dos tardes, se notan a diario.
3. **Listas guardadas** (1.4), que además es lo que falta para que programar música sea útil de verdad.
4. **«La charla de una hora en quince minutos»** (2.1). Es lo único de esta lista que ningún otro reproductor
   puede copiar fácilmente, porque exige tener ya la transcripción, el significado y el corte en el mismo sitio.
5. **Clips que respetan la frase** (2.3) y **«no me cuentes el final»** (2.5): pequeños y memorables.

Lo que **no** propongo: control por voz (se usa dos veces y se abandona), mandos CEC (no se puede probar aquí),
una tienda de extensiones (multiplica el soporte sin añadir nada que no se pueda hacer ya) y descargar torrents,
que Ser ya descartó.
