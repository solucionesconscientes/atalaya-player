# Atajos de teclado de MPV-UOS

Configurados en `mpv-config/input.conf` (validado por `tests/test_atajos.py`). Los que llevan `#!` aparecen también en el
menú completo de uosc. Los atajos por defecto de mpv siguen activos salvo que se indique lo contrario.

## Menús de MPV-UOS
| Tecla | Acción |
|---|---|
| `MBTN_RIGHT`, `MENU`, `alt+m` | Menú principal **MPV-UOS** (buscar, abrir, continuar viendo, TV y radio, yt-dlp, lista, subtítulos, audio, capítulos, captura, salir) |
| `alt+p` | Paleta global: comandos (todas las teclas con título), canales de TV/radio, vídeos recientes y acciones de mpvd; escribe para filtrar (sin acentos); con transcripción IA, sección **Diálogo** (búsqueda semántica multilingüe, Enter salta al momento) |
| `alt+h` | Recientes / continuar viendo (Tab sobre un elemento: olvidar) |
| `ctrl+m` | Menú completo generado por uosc a partir de input.conf |

## Archivo y reproducción (uosc)
| Tecla | Acción |
|---|---|
| `o` | Abrir archivo |
| `p` | Lista de reproducción |
| `c` | Capítulos |
| `s` | Subtítulos · `alt+s` cargar subtítulos |
| `a` | Pistas de audio |
| `v` | Pistas de vídeo |
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

## yt-dlp (mu-ytdl)
| Tecla | Acción |
|---|---|
| `alt+y` | Menú yt-dlp (solo audio, calidad, descargar, descargas, estado) |
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
