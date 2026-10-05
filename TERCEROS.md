# Software y datos de terceros

Atalaya Player es MIT (ver [LICENSE](LICENSE)), pero se apoya en programas y
datos con sus propias licencias, que no cambian por estar aquí. Esto es lo que
usa, cómo lo usa y bajo qué licencia.

| Qué | Cómo se usa | Licencia |
|---|---|---|
| [mpv](https://mpv.io/) | el del sistema, sin fork y sin enlazar: el programa corre dentro de él como scripts | GPL-2.0-or-later / LGPL-2.1-or-later |
| [uosc](https://github.com/tomasklaen/uosc) | vendorizado tal cual, usado por su API pública; sin modificar | LGPL-2.1 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | proceso aparte, descargado con su suma de verificación | Unlicense |
| ffmpeg | proceso aparte, el del sistema | LGPL/GPL según su compilación |
| [whisper.cpp](https://github.com/ggerganov/whisper.cpp) | binario propio en `vendor/`, proceso aparte | MIT |
| Modelos Whisper y OPUS-MT | se descargan, no se redistribuyen | la de cada modelo (OPUS-MT, CC-BY 4.0) |
| [TDTChannels](https://github.com/LaQuay/TDTChannels) e [iptv-org](https://github.com/iptv-org/iptv) | las listas se descargan en tiempo de uso; este repositorio no las contiene | de sus proyectos |
| [Radio Browser](https://www.radio-browser.info/) | se consulta su API | de su proyecto |
| [SponsorBlock](https://sponsor.ajay.app/) | se consultan sus datos de tramos patrocinados | **CC BY-NC-SA 4.0** |

## SponsorBlock, con detalle

La licencia de los datos de SponsorBlock (CC BY-NC-SA 4.0) exige atribución y
limita el uso comercial **de esos datos**. Por eso:

- La atribución está **dentro del programa**, al final del menú de Ayuda, y no
  solo en este archivo: una atribución que hay que ir a buscar a otro sitio no
  es una atribución.
- El programa solo los consulta, no los redistribuye ni los guarda para
  servirlos a nadie.
- Al pedirlos solo se envía un prefijo de 4 caracteres del hash del vídeo, así
  que SponsorBlock no sabe qué se está viendo.
