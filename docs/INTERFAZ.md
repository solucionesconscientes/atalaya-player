# La barra y la línea de tiempo (H52)

Diseño pedido por Ser el 2026-10-02: «que la opción de cortar segmento o reproducir en bucle esté en iconos en la
barra… las notas deberían poder hacerse en la línea de tiempo y aparecer en la línea de tiempo… piensa qué otros
iconos importantes pondrías y cómo simplificarlo al máximo».

## Lo que se puede hacer de verdad (comprobado, no supuesto)

Antes de diseñar nada, lo que la API pública de uosc permite (versión vendorizada 5.13, leída el 2026-10-02):

- **Botones propios en la barra**: `button:<nombre>` en la opción `controls`, y el script los gobierna con
  `set-button`. Un botón admite `icon`, `active`, `badge`, `tooltip`, `command` y **`hide`**
  (`elements/ManagedButton.lua`). O sea: estado, un número encima y desaparecer cuando no viene a cuento.
- **La barra NO se puede rehacer en caliente.** `Controls:init_options()` solo corre en `init()` y no hay nada que
  escuche cambios de opciones, así que la idea de «un modo edición que cambia la barra entera» no es posible sin
  parchear uosc. Se descarta: lo que hace el mismo papel es `hide`, botón a botón.
- **La línea de tiempo dibuja `chapter-list`** (un rombo por capítulo, con su título al pasar por encima) y
  **colorea tramos** cuyo título case con `chapter_range_patterns`, desde ese capítulo hasta el siguiente.
- **`chapter-list` es de lectura y escritura** en mpv 0.41 (manual: `chapter-list (RW)`), y en este proyecto ya se
  escribía: `mu-subs` pone ahí los capítulos por tema. Esa es la vía para marcar cosas en la línea de tiempo **sin
  tocar uosc**.
- **A y B del bucle ya se dibujan solos** en la línea de tiempo (`Timeline.lua`, `state.ab_loop_a/b`), porque salen
  de las propiedades `ab-loop-a` y `ab-loop-b` de mpv. No hay que dibujar nada.

De ahí sale una regla que no estaba escrita y que ahora sí: **`chapter-list` tiene un solo dueño**. Cada script de
mpv corre en su propio estado de Lua, así que dos que lo escriban se pisan sin enterarse. Ese dueño es `mu-marks`.

## Para qué es la barra

La barra es para **lo que se pulsa con la película andando**, cuando ir al menú rompe el momento. Todo lo demás va
al menú — no porque el menú sea mejor, sino porque un botón que se pulsa una vez al mes cuesta atención todos los
segundos que está ahí.

Tres preguntas para que un botón se quede:

1. **¿Se pulsa a media película?** Si no, al menú.
2. **¿Dice algo de un vistazo?** Estado, un número, o desaparece. Si no dice nada, es solo un atajo: eso lo hace una
   tecla sin ocupar sitio.
3. **¿Aplica ahora mismo?** Si no, `hide`.

## Dos reglas que salieron de las pruebas de Ser

**1. Lo que pasa por detrás tiene que verse.** «Guardar los tramos no hace nada» era falso —los archivos se
creaban— pero iban a una carpeta que nadie había visto y, al unir, se recodificaba en silencio. Desde fuera eso es
indistinguible de estar roto. Mientras haya trabajo en marcha hay un **icono con el número de tareas** en la barra
(y por dónde va la primera al pasar por encima); al pulsarlo, la lista. Sin trabajo, el icono no está.

**2. Una opción que no puede funcionar ahora no se ofrece como si pudiera.** Se queda —esconderla haría pensar que
no existe— pero apagada, **diciendo por qué** y señalando lo que sí sirve. Aplicado en: tramos con la TV puesta
(«es la TV o un vídeo de internet, no un archivo tuyo» → *usa Grabar*), los mandos del invitado sin control, «ir a
un minuto» sin duración y convertir un vídeo de internet. El resto del menú está por repasar con este criterio.

## La barra que queda

Agrupada por significado y ordenada por frecuencia, que es lo que no estaba: hoy «grabar» está pegado al menú y el
botón de «solo audio» vive entre los de pistas.

| Grupo | Iconos | Cuándo se ven |
|---|---|---|
| Mover | reproducir/pausa · anterior · siguiente | los dos últimos, con lista |
| Lo que estás viendo | subtítulos · pistas de audio · saltar intro | audio o vídeo; «saltar» solo dentro de un tramo |
| Ritmo | velocidad | audio o vídeo |
| Ritmo | velocidad · **ir a un minuto** | audio o vídeo con duración |
| **Lo que haces con ello** | **bucle · tramos · lista de tramos · nota** · grabar | audio o vídeo con duración |
| **Lo que está pasando** | **tareas en marcha** | solo mientras hay trabajo |
| Salir | menú · pantalla completa | siempre |

**Lo que entra**, y por qué pasa las tres preguntas:

- **Bucle** (`repeat`): se pulsa a media película, tiene estado visible (A puesta / repitiendo) y la línea de tiempo
  ya enseña A y B sola.
- **Tramos** (`content_cut`): se pulsa a media película, el **número de tramos elegidos va de insignia** en el icono
  y los tramos se ven pintados en la línea de tiempo.
- **Nota** (`edit_note`): se pulsa a media película —«esto de aquí»— y la marca cae en la línea de tiempo.

**Lo que sale de la barra**: «solo audio» (`videocam_off`). Es una decisión que se toma una vez por vídeo, solo
aplica a vídeos de internet, y su icono se lee como «cámara apagada», que hoy significa otra cosa para todo el
mundo. Sigue en `alt+a` y en el menú.

**Lo que NO se añade, y por qué**: un control de volumen (uosc ya tiene el suyo al lado), una rueda de ajustes (eso
es el menú), captura de pantalla (es una tecla; nadie la busca en la barra), enviar a la tele (una vez por sesión,
al menú) y compartir (igual). Todos fallan la pregunta 1 o la 2.

## La línea de tiempo como sitio de trabajo

Lo que tiene tiempo se dibuja **en el tiempo**, no solo en una lista dentro de un menú:

- **A y B** mientras eliges, de mpv, dibujados por uosc.
- **Los tramos ya elegidos**, pintados con el azul de la marca, de capítulo de inicio a capítulo de fin.
- **Las notas**, un rombo cada una con su texto al pasar por encima.

Las marcas conviven con los capítulos de verdad de la película: `mu-marks` guarda los originales y los vuelve a
poner al quitar las marcas o al cambiar de archivo. Efecto lateral aceptado y documentado: las teclas de capítulo
también saltan de nota en nota y de tramo en tramo, que es justo lo que se quiere cuando estás trabajando con el
vídeo.

## Los gestos

| Gesto | Qué hace |
|---|---|
| Botón **tramos** (o `alt+x`) | 1ª vez: «desde aquí». 2ª: «hasta aquí» y el tramo entra en la lista |
| Botón **bucle** (o `l`) | Repite lo que haya elegido; sin nada elegido, pone A y luego B |
| Botón **nota** (o `alt+n`) | Caja de texto; la nota se guarda con el segundo en el que estabas |
| Menú *Tramos elegidos…* | Guardar cada uno por su lado o todos unidos; vídeo o solo audio; quitar uno; vaciar |

El bucle y los tramos comparten la elección: marcas una vez, y luego decides si la repites o te la guardas. Es la
misma pregunta («qué trozo») contestada una sola vez.
