# MPV-UOS — visión

Reproductor multiplataforma (Linux/Wayland-KDE primero; Windows y macOS después) sobre mpv + uosc, sin fork de mpv.
Principios: local-first; scripts Lua finos + daemon mpvd; caché por hash de archivo; formatos abiertos
(SRT/ASS/VTT, capítulos, EDL de mpv, media segments de Jellyfin, Markdown); degradación elegante (sin mpvd → lo básico funciona;
sin GPU → modelos pequeños).

## Arquitectura
1. mpv ≥0.41: render gpu-next, filtros lavfi, hooks, JSON IPC.
2. Scripts Lua (mpv-config/scripts): uosc (UI), thumbfast, mu-core (puente con mpvd) y módulos mu-*.
3. mpvd (Python): planificador con prioridades (subtítulos inminentes > acciones interactivas > precálculo > indexado);
   servicios iptv, ytdl, asr, translate, segments, library, index, mcp, remote; caché SQLite; API JSON-RPC 2.0 con `capabilities`.
4. Clientes: agentes vía MCP; móvil vía PWA emparejada por QR; MPRIS/KDE Connect.
Límites reales de mpv: los scripts no leen PCM de audio (mpvd decodifica en paralelo con ffmpeg); a los fotogramas solo se
accede con screenshot-raw; un hilo por script; osd-overlay es caro.

## Requisitos prioritarios de Ser
### N1 · TV y radio
- España: TDTChannels TV https://www.tdtchannels.com/lists/tv.m3u8 — comprueba si existen radio.m3u8 y tvradio.m3u8 en la misma ruta y úsalas.
- Mundo: iptv-org https://iptv-org.github.io/iptv/index.m3u (TV) — comprueba sus índices por país/categoría/idioma para filtrar.
  Si iptv-org no cubre radio, añade la API abierta de Radio Browser (api.radio-browser.info) para radio mundial.
- Navegación fuente → país/grupo → canal, búsqueda instantánea, favoritos, recientes, zapping ±1, logos si es viable,
  EPG XMLTV (url-tvg) en fase posterior; radio con título ICY en pantalla y grabación del directo.
- Traducir #EXTVLCOPT/#KODIPROP (user-agent, referrer, cabeceras) a opciones de mpv por archivo.
### N2 · yt-dlp avanzado
- Reproducir en modo vídeo o solo audio (conmutable en caliente conservando la posición).
- Menú de calidad con TODOS los formatos (id, contenedor, códecs, resolución, fps, HDR, bitrate, tamaño).
- Descargar: formato exacto o combinación vídeo+audio; solo audio en códec original sin recodificar; conversión a mp3/opus/m4a/flac/wav
  con bitrate elegible (CBR 96–320 kbps o VBR); subtítulos, capítulos, miniatura y metadatos incrustados opcionales; playlists;
  cola con progreso, cancelación y reintento; carpeta y plantilla de nombre configurables.
- yt-dlp siempre actualizado (binario vendorizado) con los requisitos que exija hoy (p. ej. runtime JS para YouTube).

## Catálogo (IDs de referencia)
- A Plataforma: A1 sidecar mpvd · A2 caché por hash · A3 gestor de plugins en uosc · A4 manifiesto y permisos · A5 perfiles de contexto ·
  A6 sincronización de configuración · A7 guardián de rendimiento
- B IA: B1 subtítulos en vivo con look-ahead · B2 pre-subtitulado · B3 subtítulos en directos · B4 traducción offline contextual ·
  B5 resincronización por ASR · B6 OCR PGS/VobSub · B7 búsqueda semántica en diálogo · B8 búsqueda visual · B9 capítulos automáticos ·
  B10 salto de intro/créditos/recaps · B11 "¿qué me he perdido?" · B12 pregúntale al vídeo (RAG) · B13 realce de diálogo · B14 modo noche ·
  B15 reducción de ruido de voz · B16 doblaje sintético · B17 audiodescripción · B18 etiquetas SDH · B19 diarización
- C Idiomas/estudio: C1 subtítulos duales clicables · C2 diccionario offline · C3 Anki (con mpvacious) · C4 repetir frase · C5 shadowing ·
  C6 velocidad inteligente · C7 transcripción navegable · C8 notas → Markdown/Obsidian · C9 diapositivas a PDF · C10 clips/GIF · C11 vocabulario i+1
- D Imagen/sonido: D1 perfiles automáticos · D2 A/B de shaders · D3 fallback de shaders · D4 HDR/DV automático · D5 binaural · D6 AutoEQ ·
  D7 perfil auditivo · D8 protección fotosensible · D9 diagnóstico de tirones · D10 interpolación IA
- E Social: E1 watch party Syncplay · E2 watch party WebRTC · E3 mando QR/PWA · E4 MPRIS/KDE Connect · E5 handoff · E6 casting · E7 co-anotación
- F Biblioteca: F1 biblioteca sin servidor · F2 continuar viendo por hash · F3 series · F4 scrobbling · F5 Jellyfin/Plex · F6 búsqueda global
- G UX uosc: G1 paleta de comandos · G2 timeline enriquecida · G3 miniaturas con diálogo · G4 gestos · G5 ambilight · G6 PiP ·
  G7 onboarding/inicio · G8 accesibilidad · G9 panel lateral
- H/P Agentes y privacidad: H1 MCP rico · H2 permisos MCP · H3 automatizaciones · P1 local-first · P2 incógnito · P3 panel de conexiones
- I Disruptivas: I1 supercut · I2 filtros de contenido personales · I3 visionado adaptativo · I4 versión podcast · I5 resumen elástico ·
  I6 subtítulos de lectura cómoda · I7 rebobinado semántico · I8 traducción de rótulos · I9 perfil "TV de los abuelos"
TOP 10: B1+B2 · B7+G1 · B10 · B9+B11 · H1+H2 · B4 · C1–C3 · B13 · E3+E5 · I1+I5
