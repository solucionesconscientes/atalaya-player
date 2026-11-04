# cloudflared

Componente: **cloudflared** (https://github.com/cloudflare/cloudflared), versión fijada en `vendor.lock`
(`CLOUDFLARED_VERSION`).

Licencia: **Apache-2.0**. El texto completo está en `LICENSE`, en este mismo directorio.

Uso en Atalaya Player: es un binario externo que mpvd ejecuta como proceso aparte para abrir un túnel rápido de
Cloudflare mientras hay una sala de Compartir abierta. Se distribuye **sin modificar**. Si el paquete no lo lleva
dentro, mpvd lo descarga desde la URL fijada en `vendor.lock` y comprueba su SHA-256 antes de ejecutarlo.
