---
slug: atalaya   # la URL es /atalaya: la fija brand.json y la enseña la app (ADR-084)
title: "Atalaya Player: TV, radio, vídeos y ver juntos, en un reproductor"
description: "Reproductor libre sobre mpv: TV y radio en abierto de España y del mundo, vídeos de YouTube sin anuncios y salas para ver lo mismo a la vez con quien quieras."
nombre: Atalaya Player
nombre_interno: MPV-UOS
keyword_principal: "ver la tele en el ordenador gratis"   # volumen por verificar (no medible desde el repo)
keywords_secundarias:
  - "reproductor iptv gratis"
  - "ver tdt en el pc"
  - "escuchar radio online en el ordenador"
  - "ver una película a la vez a distancia"
  - "reproductor mpv con interfaz"
version: "0.1.0"   # pyproject.toml y nombre de los paquetes de dist/; sin etiquetas git ni release publicada
fecha_actualizacion: 2026-10-05
repo: https://github.com/solucionesconscientes/atalaya-player
licencia: MIT   # LICENSE añadido el 2026-10-05, con los avisos de terceros
estado_contenido: PUBLICADO como /atalaya el 2026-10-05; nombre, licencia, repo y atribución resueltos
---

<!-- Regla para Claude Code: toda frase marcada [VERIFICAR] se confirma en el código o se elimina. No se publica ninguna función que no exista. -->

<!-- ============ 1. HERO ============ -->

# Atalaya Player
### La tele, la radio, tus vídeos y ver juntos, en un solo reproductor

**Más de 12.000 canales de TV y emisoras de radio de España y de medio mundo (a 2026-10-05), vídeos de YouTube que se saltan los patrocinios y salas para ver lo mismo a la vez con quien quieras.** Sobre mpv, un motor de reproducción muy usado y ligero.

[Hero: vídeo en bucle `atalaya-demo.mp4`: elegir un canal → cambiar a radio → abrir un vídeo de YouTube → crear una sala y copiar el enlace (lo graba Ser; no existe aún)]

**[Descargar]**(#instalar) · [Ver el código](https://github.com/solucionesconscientes/atalaya-player)

Linux (probado) · Windows y macOS (hechos, sin probar en un equipo real) · Software libre (MIT) · Versión 0.1.0

<!-- ============ 2. QUÉ RESUELVE ============ -->

## Qué resuelve

Para ver la tele en el ordenador se suele acabar con una pestaña por cadena. La radio va en otra web, los vídeos en otra, y si quieres ver algo con alguien que está lejos, cada uno le da al play por su cuenta y nunca coinciden.

Atalaya Player lo junta todo en un reproductor: abres un canal, una emisora, un vídeo o un archivo, y y puedes copiar la dirección de cualquier canal para pasársela a quien quieras.

<!-- ============ 3. QUÉ HACE ============ -->

## Qué hace

**TV y radio en abierto.**

- Los canales y emisoras de España, con la lista de [TDTChannels](https://github.com/LaQuay/TDTChannels) (576 de TV y 1.041 de radio el 2026-10-05).
- Los de muchos otros países, con la lista general de [iptv-org](https://github.com/iptv-org/iptv) (11.138 entradas el 2026-10-05), por país y categoría. No lleva filtro propio: carga la lista tal cual la publica iptv-org, y los canales caídos o con bloqueo geográfico pueden aparecer.
- Radio de todo el mundo con [Radio Browser](https://www.radio-browser.info/).
- Buscador sin acentos, favoritos, recientes, guía de programación y grabación programada, y tus propias listas M3U.
- Sobre cualquier canal, `Tab` abre sus acciones, entre ellas **copiar su URL**.

**Vídeos y audio de internet.** Gracias a yt-dlp, reproduce vídeos de YouTube y de otras webs que yt-dlp soporta, o solo su audio, para escucharlos como un pódcast.

**Sin patrocinios.** En los vídeos de YouTube salta los tramos que la comunidad de SponsorBlock ha marcado (patrocinios, autopromoción…). Para pedirlos solo envía un prefijo de 4 caracteres del hash del vídeo: SponsorBlock no sabe qué ves. No bloquea los anuncios de YouTube.

**Descargar lo tuyo.** Guarda vídeo o audio eligiendo entre los formatos que ofrece el vídeo (con su códec, resolución y tamaño), o con presets: MP3, Opus, M4A, FLAC, WAV, 1080/720/480/360p.

**Ver juntos.**

- Creas una sala, compartes el enlace y los demás ven lo mismo que tú, sincronizado, **en su navegador** (móvil u ordenador) sin instalar nada, o en su propio reproductor (VLC, mpv o Atalaya Player).
- La sala se abre en tu propio equipo: no hay cuentas ni un servidor de la aplicación. Para entrar desde fuera de casa se abre un túnel gratuito de Cloudflare que dura lo que dura la sala.
- En las salas privadas los invitados pueden pausar y saltar desde el principio; si prefieres llevar tú los mandos, lo apagas y te piden el control. Hay chat y reacciones.
- También hay sala pública «solo ver», sin nombres ni chat.
- Funciona con archivos tuyos, vídeos de internet y TV.
- Si el invitado abre un archivo tuyo en su reproductor, lo ve en calidad original.

**Todo lo de mpv.** Cualquier formato, aceleración por hardware y una interfaz moderna con uosc, en vez de la ventana desnuda de mpv.

**Subtítulos con IA en tu equipo.** Para archivos locales, transcribe el audio con whisper.cpp sin enviar nada a internet, y puede traducirlos sin conexión.

<!-- ============ 4. DEMOSTRACIÓN ============ -->

## Así se usa

**El telediario de las nueve.** Abres Atalaya, entras en España · TV, en "Generalistas", y pinchas en La 1. Si quieres mandárselo a alguien, copias la URL del canal con `Tab`.

**La radio mientras trabajas.** Pasas a "España · Radio", eliges una emisora y minimizas. Se queda sonando.

**Una charla de una hora para escuchar sin conexión.** Pegas el enlace del vídeo, eliges "solo audio, Opus" y lo descargas.

**La peli del viernes con tu hermano, que vive en otra ciudad.** Creas una sala, le mandas el enlace por WhatsApp y lo abre en el navegador del móvil o del ordenador. Los dos veis lo mismo a la vez y cualquiera de los dos puede pausar.

[Capturas con pie: anexo B]

<!-- Sección «Datos» eliminada: no se han hecho medidas nuevas en esta máquina. Los únicos números reales están en las preguntas frecuentes (canales por lista, 2026-10-05). -->

<!-- ============ 6. INSTALAR ============ -->

## Instalar {#instalar}

Hoy no hay ninguna descarga publicada ni repositorio de paquetes: los paquetes existen (se construyen en `dist/`), pero no están alojados en ningún sitio. Estas son las vías que funcionan con el código actual:

```bash
# Linux, desde el repositorio (sin sudo; necesita mpv ≥ 0.41, ffmpeg y uv)
git clone https://github.com/solucionesconscientes/atalaya-player.git && cd atalaya-player
tools/install.sh                              # menú de aplicaciones + lanzador en ~/.local/bin
```

- **Linux, paquetes** (`tools/build_deb.sh`, `tools/build_appimage.sh`): `.deb` amd64/arm64 y AppImage x86_64/aarch64. Solo se ha probado el x86_64/amd64; los de ARM no se han ejecutado nunca. Ninguno lleva mpv: se usa el del sistema.
- **Windows**: `.zip` portable con `EMPEZAR-AQUI.cmd`, que instala mpv con winget si falta. Construido, **sin abrir en un Windows real**.
- **macOS**: solo con Homebrew y el repositorio (`brew install mpv ffmpeg uv chromaprint`); no hay `.dmg`. Sin probar en un Mac.

Requisitos: Linux (Ubuntu con KDE/Wayland es donde se ha desarrollado), mpv 0.41 o superior del sistema, ffmpeg. yt-dlp lo descarga e instala el instalador (con su suma de verificación) y se actualiza solo; para YouTube conviene tener `node` ≥ 22 o `deno`. Para salas desde internet, `cloudflared` es opcional y se instala aparte (`MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh`).

> ¿Prefieres que te lo deje instalado en tu ordenador o en la tele del salón, con tus canales favoritos ya puestos? Lo hago en remoto, como servicio. [Escríbeme](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+quiero+que+me+instales+Atalaya+Player.+Mi+equipo%3A&type=phone_number&app_absent=0).

<!-- ============ 7. PARA QUIÉN NO ES ============ -->

## Para quién no es

- **No da canales de pago ni plataformas.** Las listas que trae son públicas y el reproductor no comprueba si una emisión es legítima; no reproduce contenido con DRM. Si buscas fútbol de pago o plataformas, esto no es.
- **No es un centro multimedia completo.** Tiene modo salón (pantalla grande, mando de consola, mando por el móvil y envío a la tele por DLNA), pero la experiencia de sofá con add-ons y mando infrarrojo es la de Kodi.
- **No es un servidor de tu biblioteca de películas** para todos tus dispositivos. Tiene una Biblioteca local, pero para verla desde cualquier sitio, Jellyfin o Plex.

| Si necesitas | Usa |
|---|---|
| Centro multimedia con mando, para la tele | [Kodi](https://kodi.tv/) |
| Tu biblioteca de pelis y series en todos tus dispositivos | [Jellyfin](https://jellyfin.org/) |
| Solo listas IPTV, sin nada más, en Linux Mint | Hypnotix |
| Ver juntos con cada uno su propio reproductor (mpv, VLC) | [Syncplay](https://syncplay.pl/) |

> Si lo que necesitas está justo en esa lista, también lo hago a medida.

<!-- ============ 8. BAJO EL CAPÓ ============ -->

## Bajo el capó

- **mpv** (el del sistema, ≥ 0.41, sin fork) como motor y **uosc 5.13** como interfaz, usada solo por su API pública.
- **24 scripts Lua** (`mu-*`) finos dentro de mpv, y un servicio aparte en **Python 3.12** (`mpvd`, asyncio) que hace lo pesado. Hablan por JSON-RPC 2.0 sobre socket Unix (named pipe en Windows).
- **yt-dlp** para internet, **ffmpeg** para grabar y convertir, **whisper.cpp** para subtítulos.
- **Listas**: `mpvd` descarga las M3U (TDTChannels, iptv-org, las tuyas) con caché por ETag y 12 h de vida, y funciona sin conexión con la última copia. Las cabeceras `#EXTVLCOPT`/`#KODIPROP` se traducen a opciones de mpv. Radio Browser se consulta por su API.
- **Salas**: `mpvd` levanta un servidor HTTP local; el estado (posición, pausa, velocidad, archivo) llega a los invitados por SSE y su página ajusta el reproductor. El enlace lleva el token en el fragmento (`#k=…`), que el navegador nunca envía al servidor. No es P2P ni usa WebRTC ni un servidor de señalización: para salir a internet se abre un túnel rápido de Cloudflare (`cloudflared`, sin cuenta), y el anfitrión es quien sirve el vídeo.
- **Copiar un canal**: se copia la URL del flujo. Existen enlaces `mpv-uos://open?path=<url>` para abrir una página o vídeo en el reproductor, pero no enlaces propios por canal o categoría.

[El código está en GitHub](https://github.com/solucionesconscientes/atalaya-player).

> Este es el tipo de software que hago por encargo: reproducción, streaming y comunicación en tiempo real entre equipos.

<!-- ============ 9. BLOQUE DE ENCARGO ============ -->

## ¿Lo necesitas a tu medida?

Atalaya Player lo he hecho yo, y el código está abierto para que veas cómo trabajo. Si tu caso se parece pero no es exactamente esto, puedo:

- **Montarte una pantalla para tu negocio** (bar, sala de espera, tienda) con tus canales, tu radio o tus vídeos en bucle, que se maneje sola.
- **Hacerte un reproductor con tu marca**: los canales de tu televisión local, tu radio o tu academia, con su lista, sus enlaces y su logo.
- **Añadirle lo que te falta**: tus propias listas, grabar un programa a una hora, integración con tu web.
- **Salas para ver o escuchar juntos** en tu proyecto: formación, una comunidad, una parroquia, un club.
- **Dejártelo instalado y configurado** en tus equipos, en remoto.

Cuéntame qué tendría que hacer y te digo si se puede, cuánto costaría y si ya existe algo que lo haga.

**[Escríbeme por WhatsApp](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+vengo+de+la+p%C3%A1gina+de+Atalaya+Player.+Necesito%3A&type=phone_number&app_absent=0)** · o por correo · [Ver todos los servicios](/servicios)

<!-- ============ 10. FAQ ============ -->

## Preguntas frecuentes

**¿Es legal?**
Reproducir es legal; lo que importa es de dónde viene cada emisión. Las listas por defecto recogen emisiones que las cadenas publican gratis, y no las aloja este proyecto: se cargan de sus repositorios públicos. El reproductor no comprueba los derechos de cada una, y puedes añadir tus propias listas, de las que respondes tú.

**¿Cuántos canales tiene?**
El 2026-10-05: TDTChannels, 576 de TV y 1.041 de radio; iptv-org, 11.138 entradas de TV (sin filtro, con enlaces repetidos o caídos). A eso se suma Radio Browser. Las listas se vuelven a descargar solas cuando pasan 12 horas y se pueden actualizar a mano.

**¿Puedo descargar vídeos de YouTube?**
Sí, vídeo o solo audio, en el formato y la calidad que elijas. Úsalo para tu propio contenido, contenido con licencia libre o copias para tu uso personal. Descargar va contra las condiciones de uso de YouTube, y esa responsabilidad es de quien descarga.

**¿Cuánta gente cabe en una sala para ver juntos?**
En una sala privada, 12 invitados a la vez. En una pública «solo ver», 20 por defecto, configurable hasta 100. Todo sale de tu equipo y de tu conexión: cuantos más, más subida necesitas. Esos son los límites del código; no hay una prueba con tantas personas.

**¿Los demás necesitan instalar algo para entrar en mi sala?**
No. Basta un navegador con el enlace. Si quieren ver un archivo tuyo en calidad original, pueden abrirlo en VLC, mpv o Atalaya Player.

**¿Funciona en Windows y en Mac?**
Está pensado para ellos y hay paquete para Windows (.zip) y guía para macOS (Homebrew), pero solo se ha probado en Linux. Si lo pruebas, cuéntame qué tal.

**¿Cuánto cuesta?**
Nada. Es software libre. Lo que cobro es instalarlo y configurarlo por ti, adaptarlo o hacerte algo a medida.

<!-- ============ 11. CRÉDITOS Y LICENCIA ============ -->

## Créditos, licencia y transparencia

Atalaya Player se apoya en proyectos libres que hacen la parte difícil:

- [mpv](https://mpv.io/) y [uosc](https://github.com/tomasklaen/uosc).
- [yt-dlp](https://github.com/yt-dlp/yt-dlp).
- Las listas de [TDTChannels](https://github.com/LaQuay/TDTChannels) e [iptv-org](https://github.com/iptv-org/iptv).
- Los datos de [SponsorBlock](https://sponsor.ajay.app/), con licencia CC BY-NC-SA 4.0 (confirmada en su wiki el 2026-10-05; la «NC» limita el uso comercial y exige atribución).

No tiene relación con ninguno de ellos.

Software libre, MIT. Lo he desarrollado con asistencia de IA.

<!-- ============ 12. IN ENGLISH ============ -->

## In English

**Atalaya Player** is a free media player built on mpv and uosc. It plays TV and radio from Spain (TDTChannels) and many other countries (iptv-org, unfiltered), and you can copy any channel's URL. It also plays YouTube and other sites through yt-dlp, as video or audio only, and skips sponsor segments with SponsorBlock.

It can save your own or freely licensed media. Its watch-together rooms run on the host's own machine, keep everyone in sync, and guests join from a browser with no install; a free Cloudflare quick tunnel makes them reachable from outside.

Linux tested; Windows and macOS builds exist but are untested; no public download yet, so it installs from the repository. Built by Soluciones Conscientes, which develops bespoke software: [get in touch](https://api.whatsapp.com/send/?phone=34624237848&text=Hi+Dalmau%2C+I+come+from+the+Atalaya+Player+page.+I+need%3A&type=phone_number&app_absent=0).

<!-- Pie: Portfolio · También he hecho: [PowerClock](/powerclock) · [Grabapantallas](/grabapantallas) -->

---

# ANEXO A — JSON-LD (`license` solo si se confirma la licencia; `screenshot` y `sameAs` se añaden cuando existan capturas y repo público)

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SoftwareApplication",
      "@id": "https://solucionesconscientes.es/atalaya-player#app",
      "name": "Atalaya Player",
      "description": "Reproductor libre sobre mpv con TV y radio en abierto, vídeos de internet y salas para ver juntos.",
      "applicationCategory": "MultimediaApplication",
      "operatingSystem": "Linux",
      "softwareVersion": "0.1.0",
      "dateModified": "2026-10-05",
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
      "isAccessibleForFree": true,
      "license": "https://opensource.org/license/mit",
      "author": {"@type": "Person", "name": "Dalmau Romaní", "url": "https://solucionesconscientes.es/sobre-mi"},
      "publisher": {"@type": "Organization", "name": "Soluciones Conscientes", "url": "https://solucionesconscientes.es"},
      "sameAs": []
    },
    {
      "@type": "BreadcrumbList",
      "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Portfolio", "item": "https://solucionesconscientes.es/portfolio"},
        {"@type": "ListItem", "position": 2, "name": "Atalaya Player", "item": "https://solucionesconscientes.es/atalaya-player"}
      ]
    }
  ]
}
```

El FAQPage se genera a partir de la sección de preguntas frecuentes, con el texto final de la página.

# ANEXO B — Imágenes (estado a 2026-10-05: ninguna generada en `docs/capturas/`)

No se han hecho capturas: abrir la app en esta sesión pondría una ventana en el escritorio de Ser, y las pantallas del anexo
necesitan listas descargadas, un canal en marcha y una sala abierta (que abre un túnel a internet). Lo más parecido que existe son
`web/capturas/{tv,sala,menu,puerta,subtitulos,indice}.png` (hechas el 2026-10-04 con `tools/capturas.sh`, datos vacíos, git las
ignora). Enseñan el menú de TV y radio y el de Compartir, pero con el nombre «Atalaya Player» y sobre una carta de ajuste.

| Archivo (`/img/atalaya-player/`) | Qué debe verse | Estado | Alt |
|---|---|---|---|
| `canales.webp` | Lista de canales por categorías con un canal en reproducción | falta | Atalaya Player reproduciendo un canal de TV en abierto con la lista de categorías |
| `radio.webp` | Emisoras de radio | falta | Lista de emisoras de radio en Atalaya Player |
| `enlace.webp` | Acciones de un canal (`Tab`) con «Copiar URL» | falta | Copiar la URL de un canal para compartirla |
| `descargar.webp` | Diálogo de formato, códec y calidad | falta | Elegir formato y calidad para guardar el audio de un vídeo |
| `sala.webp` | Menú Compartir (hay `web/capturas/sala.png`) o sala con invitados y petición de control | falta | Sala para ver juntos con la petición de control de un espectador |
| `og-atalaya-player.png` (1200×630) | Generar: nombre + descriptor + captura de canales | falta | Atalaya Player, TV, radio y ver juntos |
| `atalaya-demo.mp4` / `.webm` | **Lo graba Ser** (20 s): canal → radio → YouTube → sala | falta | — |

# ANEXO C — Cómo se resolvió cada duda (2026-10-05)

- **Nombre y dirección**: no eran una duda abierta. ADR-084 (2026-10-02) ya había elegido «Atalaya Player», y
  `brand.json` ya fijaba `https://solucionesconscientes.es/atalaya`, que la aplicación enseña en Ayuda y en
  Preferencias. El guion se escribió con «Alalaya» sin saberlo; corregido aquí. El repositorio sí lleva sufijo
  (`atalaya-player`) porque `solucionesconscientes/atalaya` es del proyecto OSINT Atalaya, que queda aparcado.
- **Licencia**: `LICENSE` (MIT) añadido en la raíz, con los avisos de las licencias de terceros que no cambian por
  estar aquí (mpv, uosc, yt-dlp, whisper.cpp, las listas de canales y los datos de SponsorBlock).
- **Atribución de SponsorBlock** (CC BY-NC-SA 4.0): tres filas al final del menú de Ayuda, dentro del programa. Una
  atribución que hay que ir a buscar a la web no es una atribución.
- **Filtro de iptv-org**: no hay. La página lo dice tal cual: la lista se carga como la publica iptv-org y puede
  traer canales caídos o con bloqueo geográfico.
- **Salas**: no necesitan servidor de señalización ni lo alojamos nosotros. HTTP+SSE desde el equipo del anfitrión y,
  para salir a internet, un túnel rápido de Cloudflare sin cuenta.
- **Capturas**: hechas con `tools/capturas.sh`, con el programa de verdad y una carpeta de datos vacía (sin el
  historial de nadie).
