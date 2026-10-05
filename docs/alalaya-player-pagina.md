---
slug: alalaya-player
title: "Alalaya Player: TV, radio, vídeos y ver juntos, en un reproductor"
description: "Reproductor libre sobre mpv: TV y radio en abierto de España y del mundo, vídeos de YouTube sin anuncios y salas para ver lo mismo a la vez con quien quieras."
nombre: Alalaya Player
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
repo: "[VERIFICAR: el repo local no tiene remoto configurado (git remote vacío); no consta que sea público ni su URL]"
licencia: "MIT según pyproject.toml [VERIFICAR: no hay archivo LICENSE en el repo; ver anexo C]"
estado_contenido: CONTRASTADO con el código el 2026-10-05; quedan marcas [VERIFICAR] (repo, licencia, nombre, atribución SponsorBlock)
---

<!-- Regla para Claude Code: toda frase marcada [VERIFICAR] se confirma en el código o se elimina. No se publica ninguna función que no exista. -->

<!-- ============ 1. HERO ============ -->

# Alalaya Player
### La tele, la radio, tus vídeos y ver juntos, en un solo reproductor

**Más de 12.000 canales de TV y emisoras de radio de España y de medio mundo (a 2026-10-05), vídeos de YouTube que se saltan los patrocinios y salas para ver lo mismo a la vez con quien quieras.** Sobre mpv, un motor de reproducción muy usado y ligero.

[Hero: vídeo en bucle `alalaya-demo.mp4`: elegir un canal → cambiar a radio → abrir un vídeo de YouTube → crear una sala y copiar el enlace (lo graba Ser; no existe aún)]

**[Descargar]**(#instalar) · [Ver el código]([VERIFICAR repo])

Linux (probado) · Windows y macOS (hechos, sin probar en un equipo real) · Software libre (MIT [VERIFICAR: falta LICENSE]) · Versión 0.1.0

<!-- ============ 2. QUÉ RESUELVE ============ -->

## Qué resuelve

Para ver la tele en el ordenador se suele acabar con una pestaña por cadena. La radio va en otra web, los vídeos en otra, y si quieres ver algo con alguien que está lejos, cada uno le da al play por su cuenta y nunca coinciden.

Alalaya Player lo junta todo en un reproductor: abres un canal, una emisora, un vídeo o un archivo, y y puedes copiar la dirección de cualquier canal para pasársela a quien quieras.

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

- Creas una sala, compartes el enlace y los demás ven lo mismo que tú, sincronizado, **en su navegador** (móvil u ordenador) sin instalar nada, o en su propio reproductor (VLC, mpv o Alalaya Player).
- La sala se abre en tu propio equipo: no hay cuentas ni un servidor de la aplicación. Para entrar desde fuera de casa se abre un túnel gratuito de Cloudflare que dura lo que dura la sala.
- En las salas privadas los invitados pueden pausar y saltar desde el principio; si prefieres llevar tú los mandos, lo apagas y te piden el control. Hay chat y reacciones.
- También hay sala pública «solo ver», sin nombres ni chat.
- Funciona con archivos tuyos, vídeos de internet y TV.
- Si el invitado abre un archivo tuyo en su reproductor, lo ve en calidad original.

**Todo lo de mpv.** Cualquier formato, aceleración por hardware y una interfaz moderna con uosc, en vez de la ventana desnuda de mpv.

**Subtítulos con IA en tu equipo.** Para archivos locales, transcribe el audio con whisper.cpp sin enviar nada a internet, y puede traducirlos sin conexión.

<!-- ============ 4. DEMOSTRACIÓN ============ -->

## Así se usa

**El telediario de las nueve.** Abres Alalaya, entras en España · TV, en "Generalistas", y pinchas en La 1. Si quieres mandárselo a alguien, copias la URL del canal con `Tab`.

**La radio mientras trabajas.** Pasas a "España · Radio", eliges una emisora y minimizas. Se queda sonando.

**Una charla de una hora para escuchar sin conexión.** Pegas el enlace del vídeo, eliges "solo audio, Opus" y lo descargas.

**La peli del viernes con tu hermano, que vive en otra ciudad.** Creas una sala, le mandas el enlace por WhatsApp y lo abre en el navegador del móvil o del ordenador. Los dos veis lo mismo a la vez y cualquiera de los dos puede pausar.

[Capturas con pie: anexo B]

<!-- Sección «Datos» eliminada: no se han hecho medidas nuevas en esta máquina. Los únicos números reales están en las preguntas frecuentes (canales por lista, 2026-10-05). -->

<!-- ============ 6. INSTALAR ============ -->

## Instalar {#instalar}

Hoy no hay ninguna descarga publicada ni repositorio de paquetes: los paquetes existen (se construyen en `dist/`), pero no están alojados en ningún sitio [VERIFICAR: dónde se publicarán]. Estas son las vías que funcionan con el código actual:

```bash
# Linux, desde el repositorio (sin sudo; necesita mpv ≥ 0.41, ffmpeg y uv)
git clone <URL del repo> && cd <carpeta>      # [VERIFICAR: URL pública]
tools/install.sh                              # menú de aplicaciones + lanzador en ~/.local/bin
```

- **Linux, paquetes** (`tools/build_deb.sh`, `tools/build_appimage.sh`): `.deb` amd64/arm64 y AppImage x86_64/aarch64. Solo se ha probado el x86_64/amd64; los de ARM no se han ejecutado nunca. Ninguno lleva mpv: se usa el del sistema.
- **Windows**: `.zip` portable con `EMPEZAR-AQUI.cmd`, que instala mpv con winget si falta. Construido, **sin abrir en un Windows real**.
- **macOS**: solo con Homebrew y el repositorio (`brew install mpv ffmpeg uv chromaprint`); no hay `.dmg`. Sin probar en un Mac.

Requisitos: Linux (Ubuntu con KDE/Wayland es donde se ha desarrollado), mpv 0.41 o superior del sistema, ffmpeg. yt-dlp lo descarga e instala el instalador (con su suma de verificación) y se actualiza solo; para YouTube conviene tener `node` ≥ 22 o `deno`. Para salas desde internet, `cloudflared` es opcional y se instala aparte (`MU_VENDOR_CLOUDFLARED=1 tools/vendor.sh`).

> ¿Prefieres que te lo deje instalado en tu ordenador o en la tele del salón, con tus canales favoritos ya puestos? Lo hago en remoto, como servicio. [Escríbeme](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+quiero+que+me+instales+Alalaya+Player.+Mi+equipo%3A&type=phone_number&app_absent=0).

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

[El código está en GitHub]([VERIFICAR repo: no consta que esté publicado]).

> Este es el tipo de software que hago por encargo: reproducción, streaming y comunicación en tiempo real entre equipos.

<!-- ============ 9. BLOQUE DE ENCARGO ============ -->

## ¿Lo necesitas a tu medida?

Alalaya Player lo he hecho yo, y el código está abierto [VERIFICAR: solo si se publica el repo] para que veas cómo trabajo. Si tu caso se parece pero no es exactamente esto, puedo:

- **Montarte una pantalla para tu negocio** (bar, sala de espera, tienda) con tus canales, tu radio o tus vídeos en bucle, que se maneje sola.
- **Hacerte un reproductor con tu marca**: los canales de tu televisión local, tu radio o tu academia, con su lista, sus enlaces y su logo.
- **Añadirle lo que te falta**: tus propias listas, grabar un programa a una hora, integración con tu web.
- **Salas para ver o escuchar juntos** en tu proyecto: formación, una comunidad, una parroquia, un club.
- **Dejártelo instalado y configurado** en tus equipos, en remoto.

Cuéntame qué tendría que hacer y te digo si se puede, cuánto costaría y si ya existe algo que lo haga.

**[Escríbeme por WhatsApp](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+vengo+de+la+p%C3%A1gina+de+Alalaya+Player.+Necesito%3A&type=phone_number&app_absent=0)** · o por correo · [Ver todos los servicios](/servicios)

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
No. Basta un navegador con el enlace. Si quieren ver un archivo tuyo en calidad original, pueden abrirlo en VLC, mpv o Alalaya Player.

**¿Funciona en Windows y en Mac?**
Está pensado para ellos y hay paquete para Windows (.zip) y guía para macOS (Homebrew), pero solo se ha probado en Linux. Si lo pruebas, cuéntame qué tal.

**¿Cuánto cuesta?**
Nada. Es software libre. Lo que cobro es instalarlo y configurarlo por ti, adaptarlo o hacerte algo a medida.

<!-- ============ 11. CRÉDITOS Y LICENCIA ============ -->

## Créditos, licencia y transparencia

Alalaya Player se apoya en proyectos libres que hacen la parte difícil:

- [mpv](https://mpv.io/) y [uosc](https://github.com/tomasklaen/uosc).
- [yt-dlp](https://github.com/yt-dlp/yt-dlp).
- Las listas de [TDTChannels](https://github.com/LaQuay/TDTChannels) e [iptv-org](https://github.com/iptv-org/iptv).
- Los datos de [SponsorBlock](https://sponsor.ajay.app/), con licencia CC BY-NC-SA 4.0 (confirmada en su wiki el 2026-10-05; la «NC» limita el uso comercial y exige atribución).

No tiene relación con ninguno de ellos.

Software libre, MIT [VERIFICAR: falta el archivo LICENSE]. Lo he desarrollado con asistencia de IA.

<!-- ============ 12. IN ENGLISH ============ -->

## In English

**Alalaya Player** is a free media player built on mpv and uosc. It plays TV and radio from Spain (TDTChannels) and many other countries (iptv-org, unfiltered), and you can copy any channel's URL. It also plays YouTube and other sites through yt-dlp, as video or audio only, and skips sponsor segments with SponsorBlock.

It can save your own or freely licensed media. Its watch-together rooms run on the host's own machine, keep everyone in sync, and guests join from a browser with no install; a free Cloudflare quick tunnel makes them reachable from outside.

Linux tested; Windows and macOS builds exist but are untested; no public download yet [VERIFICAR install line]. Built by Soluciones Conscientes, which develops bespoke software: [get in touch](https://api.whatsapp.com/send/?phone=34624237848&text=Hi+Dalmau%2C+I+come+from+the+Alalaya+Player+page.+I+need%3A&type=phone_number&app_absent=0).

<!-- Pie: Portfolio · También he hecho: [PowerClock](/powerclock) · [Grabapantallas](/grabapantallas) -->

---

# ANEXO A — JSON-LD (`license` solo si se confirma la licencia; `screenshot` y `sameAs` se añaden cuando existan capturas y repo público)

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SoftwareApplication",
      "@id": "https://solucionesconscientes.es/alalaya-player#app",
      "name": "Alalaya Player",
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
        {"@type": "ListItem", "position": 2, "name": "Alalaya Player", "item": "https://solucionesconscientes.es/alalaya-player"}
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

| Archivo (`/img/alalaya-player/`) | Qué debe verse | Estado | Alt |
|---|---|---|---|
| `canales.webp` | Lista de canales por categorías con un canal en reproducción | falta | Alalaya Player reproduciendo un canal de TV en abierto con la lista de categorías |
| `radio.webp` | Emisoras de radio | falta | Lista de emisoras de radio en Alalaya Player |
| `enlace.webp` | Acciones de un canal (`Tab`) con «Copiar URL» | falta | Copiar la URL de un canal para compartirla |
| `descargar.webp` | Diálogo de formato, códec y calidad | falta | Elegir formato y calidad para guardar el audio de un vídeo |
| `sala.webp` | Menú Compartir (hay `web/capturas/sala.png`) o sala con invitados y petición de control | falta | Sala para ver juntos con la petición de control de un espectador |
| `og-alalaya-player.png` (1200×630) | Generar: nombre + descriptor + captura de canales | falta | Alalaya Player, TV, radio y ver juntos |
| `alalaya-demo.mp4` / `.webm` | **Lo graba Ser** (20 s): canal → radio → YouTube → sala | falta | — |

# ANEXO C — Pendiente de decidir o comprobar

- **Nombre.** El código, la interfaz, el README, los paquetes y `brand.json` dicen **«Atalaya Player»** (sitio
  `solucionesconscientes.es/atalaya`); «Alalaya» solo aparece en este borrador. Esta página sigue el nombre pedido, pero hay que
  decidir cuál es el definitivo y renombrar el resto (o corregir esta página). Y, si se queda «Atalaya», el choque con el proyecto
  OSINT del mismo nombre es el que ya se preveía; con «Alalaya», las capturas actuales mostrarían otro nombre.
- **Repo.** `git remote -v` está vacío: no hay URL ni consta que sea público. Hay 221 commits y ninguna etiqueta.
- **Licencia.** `pyproject.toml` declara MIT, pero no hay archivo `LICENSE` ni cabeceras. Compatibilidad: mpv no va dentro de los
  paquetes (se invoca como proceso aparte), así que MIT para el código propio no choca con su GPL/LGPL. uosc (LGPL-2.1, confirmado
  en GitHub) sí va dentro del repo y de los paquetes, sin tocar: hay que incluir su texto de licencia y avisos. thumbfast, yt-dlp,
  whisper.cpp y cloudflared también se distribuyen o descargan y tienen las suyas. No es asesoría legal: decidir y añadir `LICENSE`
  y un `THIRD-PARTY` antes de publicar.
- **SponsorBlock.** Base de datos y API bajo CC BY-NC-SA 4.0 (verificado en su wiki). No hay atribución dentro de la app ni en
  `docs/` (búsqueda de «CC BY» y «atribución» sin resultados). Falta añadirla, y valorar la cláusula NC si Alalaya se ofrece como
  servicio de pago (instalación a cambio de dinero).
- **Instalación.** No hay descarga pública: dónde se alojarán `.deb`, AppImage y `.zip`.
- **Torrents.** Existieron y se quitaron el 2026-10-04 (ADR-111); no deben aparecer en la página.
- **Salas.** No hay P2P entre espectadores ni servidor de señalización. Sin prueba de carga con 12 o 100 espectadores.
- **iptv-org.** El código no filtra nada; carga `index.m3u` entero. Si se quiere «solo emisiones abiertas», hay que construirlo.
- **Plataformas.** Windows y macOS sin probar (docs/PLATAFORMAS.md); los paquetes ARM nunca se han ejecutado.
- **Medidas.** Sección «Datos» eliminada: sin medidas nuevas.
