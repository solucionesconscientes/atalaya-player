# Atajos de teclado de MPV-UOS

Configurados en `mpv-config/input.conf`, que **solo** tiene teclas (validado por `tests/test_atajos.py`). Hasta H46 ese
fichero construía además un segundo menú, el nativo de uosc (`ctrl+m`): 119 entradas, 40 en el primer nivel y cuatro
cosas repetidas. Se retiró, y lo que solo vivía allí está ahora en el menú de MPV-UOS, que es el único.
Los atajos por defecto de mpv siguen activos salvo los de la tabla
[Teclas de mpv que cambian](#teclas-de-mpv-que-cambian). *Todas las teclas* (menú principal → Herramientas) abre un
buscador con todas, y `alt+p` la paleta.

## Menús de MPV-UOS
| Tecla | Acción |
|---|---|
| `MBTN_RIGHT`, `MENU`, `alt+m`, botón ▦ | Menú principal **MPV-UOS**, el único que hay: Abrir o descargar, TV y radio, Descargas y conversión, Subtítulos, Imagen y sonido, Grabar, Herramientas y Preferencias (ocho), más «Continuar viendo» y «Resumen e índice» cuando hay algo que ver |
| `?` | Ayuda en pantalla con las teclas principales (Enter en la última fila: todas las teclas) |
| `alt+p` | Paleta global: comandos (todas las teclas con título), canales de TV/radio, vídeos recientes y acciones de mpvd; escribe para filtrar (sin acentos); con transcripción IA, sección **Diálogo** (búsqueda semántica multilingüe, Enter salta al momento) |
| `alt+h` | Recientes / continuar viendo (Tab sobre un elemento: olvidar) |

### Moverse por los menús
Todos los menús de MPV-UOS son uno solo: el título lleva las migas («MPV-UOS › TV y radio › España») y la primera fila es
**Atrás**. `⌫` (Retroceso) o `←` vuelven un nivel; en la raíz de un módulo (TV, descargas, subtítulos IA, filtros…) vuelven
al menú desde el que se abrió o, si se abrió con su tecla, al menú principal. `Esc` cierra. Con el ratón: clic en **Atrás**
o en el menú anterior (a la izquierda), y el botón «atrás» del ratón.

## Archivo y reproducción (uosc)
| Tecla | Acción |
|---|---|
| `SPACE` | Reproducir / pausa (también el botón ▶ de la barra) |
| `ctrl+o` | **Abrir o descargar**: un campo para todo (un enlace, varios, una lista, un canal, un archivo, una carpeta o, en blanco, el portapapeles) y después una sola pregunta, reproducir o descargar |
| `o` | Abrir archivo (el selector de uosc) |
| `ctrl+v` | Abrir la URL o ruta copiada (portapapeles) |
| `ctrl+u` | Abrir URL: pega o escribe un enlace («Pegar: …» con el del portapapeles); con texto, busca en YouTube |
| `ctrl+f` | Buscar en YouTube: Enter busca y Enter reproduce; Tab: añadir a la lista o descargar |
| `p` | Lista de reproducción |
| `c` | Capítulos |
| `s` | Subtítulos · `alt+s` cargar subtítulos |
| `a` | Pistas de audio · `m` silenciar |
| `v` | Mostrar / ocultar subtítulos · `ctrl+alt+v` los secundarios |
| `A` | Relación de aspecto · `d` desentrelazar (auto / sí / no) |
| `e` | Ediciones |
| `ctrl+q` | Calidad del stream (uosc) |
| `ctrl+s` | Captura de pantalla |
| `ctrl+alt+o` | Abrir la carpeta de configuración |
| `alt+o` | Mostrar el archivo en su carpeta |
| `q` | Salir (la posición se guarda: `save-position-on-quit` + historial por contenido de mpvd) |

## TV y radio (mu-iptv)
| Tecla | Acción |
|---|---|
| `alt+t` | Menú TV y radio (España TV/Radio, Mundo por país, Radio mundial, Favoritos, Recientes, Mis listas) |
| `alt+f` | Buscar canal o emisora (paleta) |
| `alt+UP` / `alt+DOWN` | Canal siguiente / anterior dentro del grupo actual |
| `alt+G` | Guía de programación del canal que estás viendo (grabar un programa desde ella) |
| `alt+r` | Grabar desde ahora / detener: directos con `stream-record`, vídeos de internet como tramo (yt-dlp) y archivos locales sin recodificar; punto rojo y contador mientras graba. Menú completo en el botón ● (capturas, solo audio, recortar tramo, carpeta) |
| `Tab` (sobre un canal) | Acciones: favorito, copiar URL |
| — (menú TV y radio mientras ves un canal) | **Audio y subtítulos del canal**: pistas con nombres claros (Versión original, Audiodescripción, para sordos); las listas marcan los canales con CC · VO · AD. La primera fila de cada lista es «Buscar en esta lista» |

## Vídeos de internet (mu-ytdl)
| Tecla | Acción |
|---|---|
| `alt+y` | Menú de vídeos de internet: abrir URL, buscar en YouTube, solo audio, calidad, **Descargar…** (una sola puerta: un enlace, veinte, una lista, un canal o un `.txt` → lista con casillas, formato de todos y formato por fila, SRT global o por fila), descargas, estado |
| `alt+a` | Solo audio ⇄ vídeo, manteniendo la posición (internet: recarga solo el audio; archivos locales: al instante, sin decodificar el vídeo) |
| `alt+q` | Calidad: todos los formatos; Enter cambia en caliente, Tab descarga ese formato |
| `alt+d` | Descargar con un preset (vídeo/audio) y opciones |
| `alt+l` | Descargas: progreso, cancelar, repetir, quitar |

## Convertir (mu-convert)
| Tecla | Acción |
|---|---|
| `alt+C` | Convertir el archivo abierto o una carpeta entera: MP4 compatible, más pequeño (H.265), web (WebM), solo audio (MP3/M4A/Opus/FLAC/WAV) o GIF; resolución, calidad, tramo A-B y subtítulos |
| `alt+T` | Tareas: descargas y conversiones juntas; Tab: cancelar, repetir, quitar, abrir la carpeta |

## Suscripciones (mu-feeds)
| Tecla | Acción |
|---|---|
| `alt+Y` | Suscripciones a canales, listas y podcasts: añadir pegando la dirección, comprobar ahora, reglas (calidad, solo audio, conservar N, borrar lo visto, tras descargar), pausar, borrar y ajustes (horario, límite, red medida) |
| `Tab` (sobre una suscripción) | Acciones: comprobar ahora, pausar / reanudar, borrar |

## Teclas por defecto de mpv más útiles
| Tecla | Acción |
|---|---|
| `SPACE` | Pausa / reproducir |
| `←` / `→` | Retroceder / avanzar 5 s · `↑` / `↓` 60 s |
| `[` / `]` | Velocidad −10 % / +10 % · `BS` velocidad normal |
| `9` / `0` | Volumen − / + · `m` silenciar |
| `f` | Pantalla completa · `ESC` salir de pantalla completa |
| `!` / `@` | Capítulo anterior / siguiente · `<` / `>` elemento anterior / siguiente de la lista |
| `l` | Bucle A-B · `L` repetir archivo |
| `z` / `x` | Retraso de subtítulos −/+ 100 ms · `ctrl+-` / `ctrl++` retraso de audio |
| `I` | Estadísticas · `` ` `` consola |
| `Q` | Salir guardando la posición (`quit-watch-later`) |

Dentro de un menú de uosc: `/` busca, `⌫` vuelve atrás o cierra, `Tab` muestra las acciones del elemento, `ctrl+v` pega.

## Subtítulos (mu-subs: pistas, internet y whisper.cpp vía mpvd)
| Tecla | Acción |
|---|---|
| `alt+i` | **Panel de subtítulos** en tres bloques: las pistas que ya hay (con tamaño y retraso a mano) · buscar en internet (la web del vídeo y OpenSubtitles, de más fiable a menos) · crear con IA al final (idioma, modelo, automático, preparar el siguiente, estado del motor) |
| `alt+c` | Iniciar / detener los subtítulos IA del archivo actual (la transcripción parcial queda en caché y se reanuda) |
| `alt+x` | Resincronizar la pista de subtítulos externa seleccionada con la transcripción IA (desfase + deriva por tramos) |
| `alt+S` | Guardar subtítulos como SRT junto al vídeo (`<vídeo>.<idioma>.srt`): la pista seleccionada (IA, traducción, resincronizada, externa o interna de texto) o, si no hay, la pista IA |
| (menú `alt+i`) | Traducir la pista seleccionada (también pistas internas de texto) con el motor Rápido (Argos) o Máxima calidad (OPUS-MT, es/ca/fr ↔ en) · Duales: original arriba + traducción abajo · Guardar subtítulos (SRT) |

## Sonido e imagen (mu-av, filtros libavfilter validados)
| Tecla | Acción |
|---|---|
| `alt+v` | Menú **Sonido e imagen**: diálogo claro, modo noche, reducción de ruido (RNNoise/afftdn), binaural para auriculares (HRTF/crossfeed), protección fotosensible, perfil ligero, diagnóstico de tirones, modelos |
| `alt+n` | Modo noche (compresor + limitador) activar/desactivar |

## Biblioteca (mu-library)
| Tecla | Acción |
|---|---|
| `ctrl+b` | **Biblioteca**: Seguir viendo, Películas, Series › temporada › episodio (✓ visto, % empezado), Buscar, Buscar subtítulos en internet, Carpetas (añadir, quitar con Tab, reescanear) y Ajustes |
| `Esc` / `Enter` | Solo durante la cuenta atrás «Siguiente episodio en 5 s»: cancelar / reproducir ya |
| (menú `ctrl+b`) | `mu_library/library-next` (siguiente episodio ahora), `mu_library/library-subs` (subtítulos de internet), `mu_library/auto-next-toggle` |

## Saltar lo que no quieres ver: intro, créditos y patrocinios (mu-intro)
| Tecla | Acción |
|---|---|
| `alt+k` | Saltar la intro o los créditos (huellas de audio con los episodios de la temporada, en la misma carpeta o en carpetas hermanas) y, en vídeos de internet, los tramos marcados en **SponsorBlock** (patrocinio, autopromoción, «suscríbete», partes sin música); en los créditos, siguiente de la lista o siguiente episodio detectado. El icono de la barra tiene tres estados: **buscando** (reloj de arena, con el % del análisis), **saltar** (resaltado, con la etiqueta del tramo) y **no hay** (el tooltip dice por qué) |
| `alt+j` | Menú: segmentos, saltar ahora, marcar inicio/final de intro y créditos, salto automático, **SponsorBlock en vídeos de internet** y **saltar los patrocinios automáticamente** (los dos activados), analizar temporada, volver a analizar, exportar segmentos (Jellyfin) |
| `Esc` | Solo durante la cuenta atrás del salto automático («Saltando intro en 3 s · Esc cancela»): lo cancela |
| (menú `alt+j`) | Marcar a mano (`mu_intro/intro-mark-start`, `intro-mark-end`, `credits-mark-start`, `credits-mark-end`): se aplica al resto de la temporada |

## Estudio (mu-study)
| Tecla | Acción |
|---|---|
| `alt+e` | Menú **Estudio**: repetir línea, velocidad inteligente, nota, exportar clip/GIF, clips recientes |
| `alt+w` | Repetir la línea de subtítulo actual en bucle (A-B sobre el cue; `l` o de nuevo `alt+w` lo quita) |
| `alt+LEFT` / `alt+RIGHT` | Pasar el bucle a la línea anterior / siguiente |
| `alt+g` | Velocidad inteligente: ×2,5 (opción `mu-study-silence_speed`) en los silencios detectados por mpvd, velocidad normal cuando hay voz |
| `alt+b` | Nota con enlace de tiempo (escribe en el cuadro y Enter; o guarda la cita del subtítulo) → `<datos>/notas/<título del vídeo>.md` |
| `alt+B` | **Mis notas**: las de este vídeo y las de todos; Enter salta al minuto, Tab edita o borra, exportar junto al vídeo o a una carpeta (Obsidian) |
| `alt+u` | Exportar el bucle A-B (o la línea actual) como clip: formato por defecto mp4 (`mu-study-clip_format`); GIF/mp3/opus desde el menú |

## Mando a distancia (mu-remote)
| Tecla | Acción |
|---|---|
| `alt+z` | Mostrar / ocultar el código QR para emparejar el móvil (la URL también aparece en pantalla; el código vale una vez y caduca a los 10 min) |
| `alt+Z` | Menú **Mando a distancia**: estado del servidor, móviles emparejados, olvidar mandos, arrancar/detener |

## Compartir: ver juntos (mu-share)
| Tecla | Acción |
|---|---|
| `alt+W` | Menú **Compartir**: crear una sala con enlace y QR (en tu red, o desde internet si enciendes el túnel), invitados (dar o quitar el control, sacar), enlace nuevo, cerrar la sala. Si un invitado pide el control, aparece un sí/no en pantalla |
| `alt+Q` | Muestra u **oculta el código QR** de la sala sobre el vídeo (también desde el menú Compartir) |

## Enviar a la tele (mu-cast)
| Tecla | Acción |
|---|---|
| `alt+E` | **Enviar a la tele**: busca las teles y altavoces de tu red (DLNA) y les manda lo que suena, desde el mismo momento; aquí se pausa. Luego: pausar, ±30 s, volumen de la tele, **Seguir viendo aquí** (vuelve al reproductor donde iba la tele) y parar |

## Modos (mu-modes)
| Tecla | Acción |
|---|---|
| `alt+F` | **Mini reproductor**: ventana pequeña sin bordes y siempre encima; la misma tecla la devuelve a su tamaño |
| — (Preferencias) | **Modo salón**: pantalla completa, menús, subtítulos y mensajes grandes; un mando de consola (gamepad) controla el reproductor: A pausa, B/Back cierra el menú, X subtítulos, Start/Guide menú, cruceta ←/→ 10 s, ↑/↓ volumen, LB/RB anterior/siguiente |
| `alt+R` | **¿Qué me he perdido?**: las frases que resumen lo que se dijo mientras la ventana estaba minimizada o en segundo plano (o en los últimos 5 min); Enter salta a ese momento. Usa los subtítulos de texto del vídeo, los que ofrece su web (en vídeos de internet) o su transcripción IA. Las dos cosas juntas están en *Resumen e índice*, en la raíz del menú y en el panel de subtítulos |
| `alt+I` | **Índice del vídeo**: secciones por significado, cada una con su título y sus frases clave, y cada línea salta a su minuto exacto. Sale de los subtítulos que ya hay (nunca lanza una transcripción) y es instantáneo: los títulos y las frases son del propio diálogo, no los escribe ningún modelo |
| (menú `alt+I`) | **Resumen en prosa**, corto o largo, escrito por un modelo local (se baja una vez, 806 MB): cada frase con su `[mm:ss]`, validado contra el subtítulo (lo que el modelo invente se quita) |
| — (Preferencias) | **Modo sencillo**: menú principal corto (Abrir, TV y radio, Subtítulos, Preferencias) y barra mínima; «Menú completo» lo quita |

## Música, audiolibros, podcasts y letras (mu-music, mu-books, mu-lyrics)
| Tecla | Acción |
|---|---|
| `alt+A` | **Audiolibros y podcasts**: capítulos (los del archivo o uno por pista), marcadores con nota (añadir, saltar, borrar con Tab), velocidad de este libro, «Seguir escuchando» y temporizador de apagado (15/30/45/60 min o al terminar el capítulo; baja el volumen poco a poco y pausa) |
| `alt+J` / `alt+L` | Atrás / adelante 30 s |
| `alt+K` | **Letra** de la canción: las líneas con su minuto (Enter salta), mostrar u ocultar; «¿Qué canción es?» y sus ajustes (internet desactivado por defecto) |
| `alt+M` | **Música**: artistas › álbumes › pistas, álbumes, géneros, buscar (sin acentos), listas M3U8 e inteligentes, cola («Reproducir a continuación», mover con `ctrl+↑/↓`, guardar como lista), historial local, carpetas y ajustes (sin cortes, fundido, volumen igualado, salida exclusiva) |

## Teclas de mpv que cambian
Estas teclas por defecto de mpv 0.41 hacen otra cosa en MPV-UOS:

| Tecla | En mpv | En MPV-UOS |
|---|---|---|
| `s` | Captura de pantalla | Pistas de subtítulos (la captura está en `ctrl+s` y `S`) |
| `p` | Pausa | Lista de reproducción (la pausa está en `SPACE`) |
| `o` | Mostrar la posición | Abrir archivo |
| `e` | Zoom (panscan) | Ediciones |
| `ctrl+v` | Añadir el portapapeles a la lista | Abrir el portapapeles ahora (sustituye lo que suena) |
| `alt+v` | Subtítulo secundario visible | Menú Sonido e imagen (el secundario está en `ctrl+alt+v`) |
| `alt+←/→/↑/↓` | Desplazar el vídeo | Estudio (repetir línea anterior/siguiente) y zapping de TV |
| Botón derecho, `MENU` | Pausa / menú contextual | Menú principal MPV-UOS |
| `?` | Página de teclas de las estadísticas | Ayuda de MPV-UOS (las estadísticas siguen en `i` / `I`) |
| Clic izquierdo | Nada (arrastra la ventana) | Igual; con Preferencias › «Pausar con un clic en el vídeo», pausa |
