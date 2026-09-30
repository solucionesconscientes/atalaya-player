# Marca de MPV-UOS

- **Nombre**: pendiente; lo decide Ser más adelante (se barajó «Sintonía»). Mientras, MPV-UOS.
- **Logo**: `logo-anillo.svg` (propuesta «C · Anillo», aprobada por Ser el 2026-09-30). Propuestas comparadas:
  https://claude.ai/artifact/Pt3zkbMfrB8auucVCW19s9 (privada de Ser).
- **Marca escrita**: el nombre en minúsculas, Bricolage Grotesque 700, con un punto medio en ámbar (p. ej. «mpv·uos»).

| Color | Hex | Uso |
|---|---|---|
| Tinta | `#0D1320` | fondo del icono |
| Pizarra | `#24324D` | pista del anillo |
| Azul señal | `#3D7BFF` | progreso, acento de interfaz |
| Ámbar en antena | `#FFB020` | estado «en directo / grabando / descargando» |
| Niebla | `#E9EEF6` | fondos claros |

Variantes (H33): `logo-sin-fondo.svg` (sobre fondos oscuros) y `logo-mono.svg` (16 px, `currentColor`: bandeja e icono
simbólico `mpv-uos-symbolic`, anillo más grueso para que se lea a ese tamaño). Iconos de la PWA: `mpvd/remote/www/icon.svg`
(= el logo) e `icon-192.png`/`icon-512.png` (`rsvg-convert -w N docs/marca/logo-anillo.svg`).

## Dónde vive el nombre
Solo en `brand.json` (raíz del proyecto): `name` (lo que ve la persona), `id` (ficheros: `mpv-uos.desktop`, icono,
MPRIS), `folder` (carpeta dentro de Vídeos/Música) y la paleta. Lo leen `mpvd/brand.py`, `mu/brand.lua` (títulos,
migas, color ámbar de «grabando»), `tools/install.sh` (`Name=` de la entrada de escritorio) y la PWA del mando (se sirve
con el nombre de `brand.json`). Cambiar `folder` cambia dónde se guardan las descargas nuevas: las anteriores se quedan
en la carpeta vieja.
