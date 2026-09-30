# Mando a distancia desde el móvil (mu-remote + mpvd)

El móvil se convierte en mando sin instalar nada: mpvd sirve una PWA (HTML+JS sin framework) por HTTP en la red local y el
reproductor muestra un código QR para emparejarlo.

## Uso
1. Con un vídeo abierto (`bin/mpv-uos video.mkv`) pulsa `alt+z`: aparece el QR y debajo la URL (`http://<IP-LAN>:8790/#t=<código>`).
2. Escanéalo con la cámara del móvil (misma wifi). La página canjea el código (vale **una vez** y caduca a los **10 min**) por una
   cookie firmada (HMAC) y queda emparejado: las siguientes veces basta con abrir la misma dirección o el icono de la pantalla de inicio
   ("Añadir a pantalla de inicio" instala la PWA).
3. Pestañas: **Control** (play/pausa, saltos, barra de progreso, volumen, velocidad, capítulos, pantalla completa, bucle A-B),
   **Canales** (favoritos, recientes y búsqueda de TV/radio; zapping ±1), **Buscar** (diálogo: búsqueda semántica si hay índice,
   si no literal sobre la transcripción), **Recientes** y **Más** (pistas de audio y subtítulos, lista de reproducción, capítulos,
   desemparejar este móvil).
4. `alt+Z` abre el menú *Mando a distancia*: estado del servidor, móviles emparejados, "Olvidar todos los mandos" (rota el secreto y
   todas las cookies dejan de valer) y arrancar/detener el servidor.

## Panel de descargas (H23)
Una segunda página del mismo servidor, `http://<IP>:8790/downloads`, con el mismo emparejamiento que el mando:
- **Desde el ordenador**: menú *Mando a distancia* → «Panel de descargas en el navegador». Abre el navegador ya emparejado
  (dirección `127.0.0.1`, donde el navegador sí permite notificaciones).
- **Desde el móvil**: una vez emparejado, pestaña **Más** → «Panel de descargas y conversiones».
- Qué hay: las descargas y conversiones en curso con su progreso en vivo (SSE, como mucho una actualización por segundo) y el
  historial; casillas para elegir varias y **Cancelar**, **Reintentar** o **Quitar** a la vez; «Vaciar historial» (los archivos
  no se borran); ▶ en una tarea terminada la abre en el reproductor; el espacio libre del disco de la carpeta de descargas.
- **Añadir**: pega o arrastra enlaces (o un `.txt` con uno por línea) en el recuadro, elige el formato (se recuerda) y pulsa
  «Descargar» (o `ctrl+Enter`).
- **Aviso al terminar**: «Avisarme al terminar» pide permiso de notificaciones. Por la red local sin HTTPS el navegador del
  móvil no las permite: el aviso sale arriba en la página, el móvil vibra y el título de la pestaña cuenta las terminadas.
  La página tiene que estar abierta.
- **«Enviar a MPV-UOS» desde el navegador** (sección plegable al final de la página): tres marcadores para arrastrar a la barra.
  «⬇ Descargar con MPV-UOS» abre `mpv-uos://download?url=<página>` (en este ordenador, con `tools/install.sh`: se pone en cola
  sin abrir el reproductor y sale una notificación del escritorio); «▶ Ver en MPV-UOS» abre `mpv-uos://open?path=<página>`
  (la reproduce); «⬇ Enviar al panel» abre esta página con el enlace puesto — sirve en cualquier equipo de la red, sin esquema —
  y **no descarga hasta que pulsas «Descargar»** (cualquier web podría abrir esa dirección).

## Seguridad
- Sin token válido o cookie firmada la API responde 401; el código del QR va en el fragmento `#` de la URL, así que no aparece en
  logs ni en cabeceras `Referer`.
- La API solo acepta una lista blanca de órdenes de reproductor (nada de comandos arbitrarios de mpv); los POST exigen que
  `Origin` coincida con `Host` y la cookie es `HttpOnly; SameSite=Strict`.
- Es HTTP sin cifrar dentro de la LAN (un certificado autofirmado haría que el móvil muestre avisos); úsalo en redes de confianza.
- El servidor solo se arranca al pedir un QR (o desde el menú) y queda activado para siguientes arranques de mpvd hasta que lo detengas
  (`autostart` en `<datos>/remote.json`).

## Configuración
| Variable | Por defecto | Qué hace |
|---|---|---|
| `MPVD_REMOTE_PORT` | `8790` (o el último usado) | Puerto TCP; si está ocupado se usa uno libre |
| `MPVD_REMOTE_HOST` | `0.0.0.0` | Interfaz donde escucha (`127.0.0.1` = solo este equipo) |
| `MPVD_REMOTE_PUBLIC_HOST` | IP LAN detectada | Dirección escrita en el QR (útil con varias interfaces o VPN) |

Opciones de script (`mpv-config/script-opts/mu-remote.conf`): `qr_seconds=120` (ocultar el QR solo), `qr_size=300`.

## Si el móvil no conecta
- Cortafuegos: con `ufw` activo y política de entrada DROP hay que abrir el puerto una vez:
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos remote'` (ajusta la subred).
- Comprueba desde el propio equipo: `curl -s http://127.0.0.1:8790/ | head -3`.
- Móvil y ordenador deben estar en la misma red (algunas wifis de invitados aíslan a los clientes).

## API (para otros clientes)
Métodos JSON-RPC de mpvd: `remote.status`, `remote.start {host?, port?}`, `remote.stop`, `remote.pair {session?, ttl?}`
(devuelve `url`, `token` y el QR en filas/rachas), `remote.forget`.
HTTP: `POST /api/pair {token}`, `GET /api/state`, `POST /api/cmd {cmd, …}`, `GET /api/channels?q=&kind=`, `GET /api/search?q=`,
`GET /api/recents`, `GET /api/tracks`, `GET /api/playlist`, `GET /api/chapters`, `GET /events` (SSE, evento `state` ≤2 Hz),
`POST /api/unpair`.
Panel de descargas: `GET /api/tasks` (filas de `tasks.list` + `disk`), `GET /events/tasks` (SSE, evento `tasks` solo al cambiar,
≤1 Hz), `POST /api/tasks/action {action: cancel|retry|remove|play, items: [{type, id}]}`, `POST /api/tasks/clear`,
`GET /api/downloads/presets`, `POST /api/downloads/add {text | urls, preset}` (→ `ytdl.download.batch`), `GET /api/disk`.
`remote.pair {path: "/downloads", local: true}` da un enlace de un solo uso al panel. Enlaces del navegador:
`mpv-uos://download?url=<url>[&preset=<id>]` → `python -m mpvd link <enlace>` (solo http/https). Detalles de la verificación (overlay ASS, QR, red) en docs/REMOTE_API.md.
