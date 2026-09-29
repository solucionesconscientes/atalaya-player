# Atajos de teclado de MPV-UOS

Configurados en `mpv-config/input.conf` (validado por `tests/test_atajos.py`). Los que llevan `#!` aparecen también en el
menú **Más opciones** de uosc (`ctrl+m`). Los atajos por defecto de mpv siguen activos salvo los de la tabla
[Teclas de mpv que cambian](#teclas-de-mpv-que-cambian). `Todas las teclas` (menú principal) abre un buscador con todas.

## Menús de MPV-UOS
| Tecla | Acción |
|---|---|
| `MBTN_RIGHT`, `MENU`, `alt+m` | Menú principal **MPV-UOS** (buscar, abrir, continuar viendo, TV y radio, yt-dlp, lista, subtítulos, audio, capítulos, captura, salir) |
| `alt+p` | Paleta global: comandos (todas las teclas con título), canales de TV/radio, vídeos recientes y acciones de mpvd; escribe para filtrar (sin acentos); con transcripción IA, sección **Diálogo** (búsqueda semántica multilingüe, Enter salta al momento) |
| `alt+h` | Recientes / continuar viendo (Tab sobre un elemento: olvidar) |
| `ctrl+m` | Más opciones: menú de uosc agrupado (Ver, Audio, Subtítulos, Velocidad, Repetir…) |

## Archivo y reproducción (uosc)
| Tecla | Acción |
|---|---|
| `SPACE` | Reproducir / pausa (también el botón ▶ de la barra) |
| `o` | Abrir archivo |
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
| `ctrl+o` | Abrir la carpeta de configuración |
| `alt+o` | Mostrar el archivo en su carpeta |
| `q` | Salir (la posición se guarda: `save-position-on-quit` + historial por contenido de mpvd) |

## TV y radio (mu-iptv)
| Tecla | Acción |
|---|---|
| `alt+t` | Menú TV y radio (España TV/Radio, Mundo por país, Radio mundial, Favoritos, Recientes, Mis listas) |
| `alt+f` | Buscar canal o emisora (paleta) |
| `alt+UP` / `alt+DOWN` | Canal siguiente / anterior dentro del grupo actual |
| `alt+r` | Grabar / detener la grabación del directo (`~/Escritorio/MPV-UOS`) |
| `Tab` (sobre un canal) | Acciones: favorito, copiar URL |

## Vídeos de internet (mu-ytdl)
| Tecla | Acción |
|---|---|
| `alt+y` | Menú de vídeos de internet (abrir URL, buscar en YouTube, solo audio, calidad, descargar, descargas, estado) |
| `alt+a` | Solo audio ⇄ vídeo, manteniendo la posición |
| `alt+q` | Calidad: todos los formatos; Enter cambia en caliente, Tab descarga ese formato |
| `alt+d` | Descargar con un preset (vídeo/audio) y opciones |
| `alt+l` | Descargas: progreso, cancelar, repetir, quitar |

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

## Subtítulos IA (mu-subs, whisper.cpp vía mpvd)
| Tecla | Acción |
|---|---|
| `alt+i` | Menú **Subtítulos IA**: iniciar/detener, idioma, modelo (descarga bajo demanda), activar automáticamente, pre-subtitular el siguiente de la lista, estado del motor |
| `alt+c` | Iniciar / detener los subtítulos IA del archivo actual (la transcripción parcial queda en caché y se reanuda) |
| `alt+x` | Resincronizar la pista de subtítulos externa seleccionada con la transcripción IA (desfase + deriva por tramos) |
| (menú `alt+i`) | Traducir la pista seleccionada a otro idioma (offline, Argos/CTranslate2) · Duales: original arriba + traducción abajo |

## Sonido e imagen (mu-av, filtros libavfilter validados)
| Tecla | Acción |
|---|---|
| `alt+v` | Menú **Sonido e imagen**: diálogo claro, modo noche, reducción de ruido (RNNoise/afftdn), binaural para auriculares (HRTF/crossfeed), protección fotosensible, perfil ligero, diagnóstico de tirones, modelos |
| `alt+n` | Modo noche (compresor + limitador) activar/desactivar |

## Saltar intro y créditos (mu-intro)
| Tecla | Acción |
|---|---|
| `alt+k` | Saltar la intro o los créditos detectados (huellas de audio entre episodios de la misma carpeta); fuera de un segmento salta al final del siguiente |
| `alt+j` | Menú **Saltar intro y créditos**: segmentos detectados, saltar ahora, salto automático de intro/créditos, volver a analizar |

## Estudio (mu-study)
| Tecla | Acción |
|---|---|
| `alt+e` | Menú **Estudio**: repetir línea, velocidad inteligente, nota, exportar clip/GIF, clips recientes |
| `alt+w` | Repetir la línea de subtítulo actual en bucle (A-B sobre el cue; `l` o de nuevo `alt+w` lo quita) |
| `alt+LEFT` / `alt+RIGHT` | Pasar el bucle a la línea anterior / siguiente |
| `alt+g` | Velocidad inteligente: ×2,5 (opción `mu-study-silence_speed`) en los silencios detectados por mpvd, velocidad normal cuando hay voz |
| `alt+b` | Nota con enlace de tiempo (escribe en el cuadro y Enter; o guarda la cita del subtítulo) → `<datos>/notas/<clave>.md` |
| `alt+u` | Exportar el bucle A-B (o la línea actual) como clip: formato por defecto mp4 (`mu-study-clip_format`); GIF/mp3/opus desde el menú |

## Mando a distancia (mu-remote)
| Tecla | Acción |
|---|---|
| `alt+z` | Mostrar / ocultar el código QR para emparejar el móvil (la URL también aparece en pantalla; el código vale una vez y caduca a los 10 min) |
| `alt+Z` | Menú **Mando a distancia**: estado del servidor, móviles emparejados, olvidar mandos, arrancar/detener |

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
