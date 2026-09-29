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
`POST /api/unpair`. Detalles de la verificación (overlay ASS, QR, red) en docs/REMOTE_API.md.
