---
slug: powerclock
title: "PowerClock: programar el apagado y el encendido en Linux"
description: "Apaga, suspende y enciende tu ordenador solo, a una hora o cuando termine una descarga o un render. Con aviso cancelable. Software libre para Linux."
keyword_principal: "programar apagado linux"   # volumen por verificar (Keyword Planner)
keywords_secundarias:
  - "apagar ordenador automáticamente linux"
  - "encender pc automáticamente a una hora"
  - "alternativa a kshutdown"
  - "apagar pc cuando termine descarga"
version: "1.0.0"
fecha_version: 2026-10-02
fecha_publicacion: 2026-10-05
fecha_actualizacion: 2026-10-05
repo: https://github.com/solucionesconscientes/powerclock
pypi: https://pypi.org/project/powerclock/
licencia: GPL-3.0-or-later
idioma: es
---

<!-- ============ 1. HERO ============ -->

# PowerClock
### Programa el apagado y el encendido de tu ordenador en Linux

**Tu ordenador se apaga solo y se enciende solo:** a una hora exacta, o cuando termina una descarga, acaba un render o dejas de usarlo. Siempre con un aviso que puedes cancelar hasta el último segundo.

[Hero: vídeo en bucle `powerclock-demo.mp4` — ver lista de imágenes. Mientras no exista: `quick.webp`]

**[Descargar el instalador]**(#instalar) · [Ver el código](https://github.com/solucionesconscientes/powerclock)

Linux · Intel/AMD y ARM · Software libre (GPL-3.0) · Versión 1.0, octubre de 2026

<!-- ============ 2. QUÉ RESUELVE ============ -->

## Qué resuelve

PowerClock apaga, suspende y **enciende** tu ordenador por sí solo, y entre medias ejecuta lo que necesites. Lo hace con reglas que siguen funcionando con la ventana cerrada, después de reiniciar e incluso sin la sesión iniciada.

Los programadores de apagado de toda la vida solo saben "apagar a las 23:30". PowerClock también sabe esperar a que algo termine, saltarse el momento si estás viendo una película y volver a encender el equipo por la mañana. Y antes de cada acción te dice qué va a pasar y cuándo.

<!-- ============ 3. QUÉ HACE ============ -->

## Qué hace

**Apagar, a tu manera.** Apagar, reiniciar, suspender, hibernar, suspensión híbrida, bloquear, cerrar la sesión o apagar la pantalla. Por defecto de forma ordenada: KDE y GNOME piden antes a las aplicaciones que guarden.

**Encender el equipo.** Programa la alarma del reloj del propio ordenador para que despierte de la suspensión, o se encienda estando apagado si la BIOS lo permite. Puede entrar en la sesión solo en ese arranque, con la pantalla bloqueada. También enciende otros equipos de la red (Wake-on-LAN).

**Cuando pase algo, no solo a una hora:**

- Cuando dejes de usar el equipo.
- Cuando termine un programa: un render, una compresión, una copia.
- Cuando la red baje porque acabó la descarga, o cuando la CPU se calme.
- Cuando la batería baje de un nivel o desenchufes el portátil.

**Hacer cosas entre medias.**

- Ejecutar programas y scripts.
- Abrir aplicaciones colocadas en una pantalla concreta o a pantalla completa.
- Controlar el reproductor y el volumen.
- Avisarte al móvil (ntfy, Telegram) y esperar tu respuesta con botones.

**Sin sustos.**

- Cuenta atrás con *Cancelar* y *Posponer 10 minutos* antes de cualquier acción de energía.
- Modo prueba que no apaga nada.
- Historial de cada ejecución con su motivo.

**Como prefieras manejarlo.** Desde un icono en la bandeja y una ventana con cuatro pestañas, desde la línea de comandos o desde cualquier programa a través de un API local.

<!-- ============ 4. DEMOSTRACIÓN ============ -->

## Una regla se lee como una frase

No hay que aprender ninguna sintaxis. Cada regla responde a cuatro preguntas: **cuándo**, **solo si…**, **esperar mientras…** y **qué hará**. El editor te las hace en ese orden.

**Copia de seguridad por la noche, y apagar.** Todos los días a las 03:00 el equipo se enciende solo. Si está enchufado, hace la copia. Si estás viendo algo o hay alguien conectado por SSH, espera, hasta dos horas. Al terminar te avisa y se apaga.

**Apagar cuando termine la descarga.** Cuando la red lleva 5 minutos por debajo de 50 kbit/s, apaga el equipo, avisando 2 minutos antes.

**La pantalla de la tienda, sola.** De lunes a viernes a las 09:00 se enciende el equipo, abre tu web o tu presentación a pantalla completa y la vuelve a abrir si alguien la cierra. A las 21:00 se apaga. Los festivos no se enciende.

**Ahorrar luz sin pensarlo.** Tras 20 minutos sin usar el ordenador, se suspende, salvo que se esté reproduciendo algo. El historial te dice cuántas horas ha estado apagado y cuánto has ahorrado.

[Capturas en cuadrícula, con pie:]

| | |
|---|---|
| `quick.webp` — **Rápido**: eliges qué y cuándo, y el botón dice lo que hará. Arriba, siempre, lo próximo que va a pasar. | `rules.webp` — **Reglas**: todas tus automatizaciones, cuándo actúan y qué toca después. |
| `editor-when.webp` — **El editor**: la regla escrita en palabras arriba y el horario sin escribir cron. | `countdown.webp` — **El aviso**: un anillo que se vacía y *Cancelar* como botón destacado, porque es lo seguro. |
| `history.webp` — **Historial**: qué se ejecutó, cómo terminó y por qué, más lo ahorrado en 30 días. | `diagnostics.webp` — **Diagnóstico**: qué funciona en tu equipo y cómo arreglar lo que no. |

<!-- ============ 5. DATOS MEDIDOS ============ -->

## Probado en un portátil real

Medido en un Dell Latitude 5480 con Kubuntu 26.04 y KDE Plasma 6 sobre Wayland:

| Prueba | Resultado |
|---|---|
| Despertar de la suspensión | A la primera, 2 s después de la alarma |
| Encender desde apagado (enchufado, BIOS de serie) | Funciona; el sistema arranca 14 s después de la alarma |
| Leer sensores | Solo los que usan tus reglas: si ninguna vigila la CPU, nunca la lee |

Que el equipo se encienda estando **apagado** depende de la BIOS. Muchos portátiles solo lo hacen enchufados a la corriente. `powerclock doctor --test-wake 120` lo comprueba en tu máquina en dos minutos.

<!-- ============ 6. INSTALAR ============ -->

## Instalar {#instalar}

### Con el instalador (no hace falta usar la terminal)

1. Descarga [`install-powerclock.sh`](https://github.com/solucionesconscientes/powerclock/releases/latest/download/install-powerclock.sh).
2. Clic derecho → *Propiedades* → *Permisos* → *Permitir ejecutar el archivo como un programa*.
3. Haz doble clic y pulsa **Instalar**. Escribe tu contraseña una sola vez, para que pueda encender el equipo.

Se descarga con su propio Python y Qt (unos 130 MB; 400 MB en disco), así que no depende de lo que tenga tu sistema. Queda en la bandeja y en el menú de aplicaciones. Actualizar no vuelve a pedir contraseña.

### Con pipx (usuarios técnicos y servidores)

```bash
pipx install "powerclock[gui]"
powerclock setup
powerclock doctor
```

En un servidor sin escritorio: `pipx install powerclock` y `powerclock service install --linger`.

**Requisitos**:

- Linux con systemd y Python 3.11 o posterior (lo traen las distribuciones actuales).
- Probado en KDE Plasma sobre Wayland; compatible con GNOME y X11.
- Funciona en Intel/AMD y ARM, también en una Raspberry Pi sin escritorio.

`powerclock doctor` te dice qué funciona en tu equipo y cómo arreglar lo que no.

> ¿Prefieres que te lo deje instalado y con tus reglas configuradas? Lo hago en remoto, como servicio. [Escríbeme](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+quiero+que+me+instales+y+configures+PowerClock.+Mi+equipo%3A&type=phone_number&app_absent=0).

<!-- ============ 7. PARA QUIÉN NO ES ============ -->

## Para quién no es

- **Windows y macOS: todavía no.** Están previstos (Windows en la 1.1, macOS en la 1.2), pero hoy solo funciona en Linux.
- **Varios equipos desde un panel central: tampoco.** Está en la hoja de ruta de la 2.0. Hoy cada equipo tiene su PowerClock.
- **Si solo quieres apagar dentro de un rato una vez**, te basta con `shutdown -h +30` en una terminal, o con KShutdown, en el que se inspiró la pestaña Rápido.

| Si necesitas | Usa |
|---|---|
| Apagar a una hora, sin condiciones ni encendido | [KShutdown](https://kshutdown.sourceforge.io/) o `shutdown` |
| Tareas programadas en un servidor, sin apagar ni encender | `cron` o temporizadores de systemd |
| Automatizar una casa entera, no un ordenador | Home Assistant (PowerClock puede avisarle por webhook) |
| Programar el apagado en Windows hoy | El Programador de tareas de Windows |

> Si lo que necesitas está en esa lista, también lo hago a medida.

<!-- ============ 8. BAJO EL CAPÓ ============ -->

## Bajo el capó

Un pequeño servicio en segundo plano, el *daemon*, funciona **con tu usuario y nunca como root**. Guarda las reglas, vigila la hora y los sensores, ejecuta los pasos y anota el historial. La ventana, la bandeja y la línea de comandos son solo clientes: cerrarlos no cambia nada.

Para encender el equipo hace falta escribir la alarma del reloj del hardware, y eso exige root. Lo hace un programa diminuto que **solo** sabe poner, quitar o leer esa alarma; lo instalas tú, viendo antes los comandos exactos. El API local solo escucha en tu propio equipo y pide un token que solo tu usuario puede leer.

Python y PySide6 (Qt). Tests contra un equipo simulado y un reloj falso, para que nunca apaguen tu máquina. [El código está en GitHub](https://github.com/solucionesconscientes/powerclock).

> Este es el tipo de software que hago por encargo: programas que hablan con el sistema, con el hardware y con otros programas, y que no fallan en silencio.

<!-- ============ 9. BLOQUE DE ENCARGO ============ -->

## ¿Lo necesitas a tu medida?

PowerClock lo he hecho yo, y el código está abierto para que veas cómo trabajo. Si tu caso se parece pero no es exactamente esto, puedo:

- **Dejarlo funcionando en los ordenadores de tu oficina o tu negocio**, en remoto: encendido por la mañana, apagado por la noche y copias de seguridad mientras nadie trabaja.
- **Montarte una pantalla o un quiosco** para una tienda, una recepción o un local, que se encienda, muestre lo tuyo y se apague solo.
- **Añadirle lo que te falta**: una acción, un sensor, una integración con Telegram, Home Assistant o tu propio sistema.
- **Hacerte una aplicación de escritorio a medida** que automatice un proceso de tu trabajo, con la misma forma de construir: con avisos, historial y nada que falle en silencio.
- **Calcular cuánto te ahorra apagar los equipos** y revisar de paso tu factura de luz.

Cuéntame qué tendría que hacer y te digo si se puede, cuánto costaría y si ya existe algo que lo haga.

**[Escríbeme por WhatsApp](https://api.whatsapp.com/send/?phone=34624237848&text=Hola+Dalmau%2C+vengo+de+la+p%C3%A1gina+de+PowerClock.+Necesito%3A&type=phone_number&app_absent=0)** · o por correo · [Ver todos los servicios](/servicios)

<!-- ============ 10. FAQ ============ -->

## Preguntas frecuentes

**¿Puede encender el ordenador estando apagado?**
Sí, si la BIOS lo permite. Usa la alarma del reloj del propio equipo. En muchos portátiles solo funciona enchufado a la corriente. `powerclock doctor --test-wake 120` lo comprueba en tu máquina.

**¿Me puede apagar el equipo mientras trabajo?**
No sin avisar. Antes de cualquier acción de energía hay una cuenta atrás de 60 segundos con *Cancelar* y *Posponer 10 minutos*. Además, cada regla puede esperar mientras se reproduce algo, hay un render en marcha o alguien está conectado.

**¿Necesita root o la contraseña cada vez?**
No. PowerClock funciona con tu usuario. La contraseña se pide una sola vez, al instalar el pequeño programa que programa la alarma de encendido. Actualizar nunca la pide.

**¿Las reglas funcionan con la ventana cerrada o después de reiniciar?**
Sí. Las guarda y ejecuta un servicio en segundo plano. Con `--linger` funcionan incluso sin nadie con la sesión iniciada.

**¿Funciona en GNOME, en X11 o en una Raspberry Pi?**
Sí. Está probado en KDE Plasma sobre Wayland y es compatible con GNOME y X11. En una Raspberry Pi funciona sin escritorio, desde la línea de comandos. En GNOME, el icono de la bandeja necesita la extensión AppIndicator.

**¿Cuánto cuesta?**
Nada. Es software libre con licencia GPL-3.0. Lo que cobro es instalarlo y configurarlo por ti, adaptarlo o hacerte algo a medida.

**¿Puedo probarlo sin que apague nada?**
Sí. Con `--dry-run`, o con el servicio en modo prueba, las acciones de energía solo se anotan en el historial.

**¿Cómo lo desinstalo?**
Desde *Diagnóstico → Desinstalar PowerClock…* o con `powerclock uninstall`. Con `--purge` también borra tus reglas y el historial.

<!-- ============ 11. CRÉDITOS Y LICENCIA ============ -->

## Licencia y transparencia

Software libre, **GPL-3.0-or-later**: puedes usarlo, estudiarlo, compartirlo y mejorarlo. Si distribuyes una versión modificada, tiene que seguir siendo libre. Su pestaña Rápido se inspiró en [KShutdown](https://kshutdown.sourceforge.io/); PowerClock es un programa distinto, escrito desde cero y sin relación con él.

Lo he desarrollado con asistencia de IA, y el historial del repositorio lo refleja. Lo que garantiza que funciona son las pruebas y las medidas de esta página, no quién escribió cada línea.

<!-- ============ 12. IN ENGLISH ============ -->

## In English

**PowerClock** is a free shutdown, wake-up and task scheduler for Linux. It shuts down, suspends and **powers on** your computer by itself, at an exact time or when a condition is met: you stop using it, a download or render finishes, the battery runs low. It runs your scripts and apps in between.

Rules are read like a sentence: *when*, *only if*, *wait while*, *what it does*. They keep working with the window closed, after a reboot and even with no one logged in. Every power action comes with a cancellable countdown, a dry-run mode and a full history.

Install with the graphical installer from the [releases page](https://github.com/solucionesconscientes/powerclock/releases/latest), or with `pipx install "powerclock[gui]" && powerclock setup`. It runs on Linux with systemd (Intel/AMD and ARM); Windows and macOS are planned.

GPL-3.0-or-later. Built by Soluciones Conscientes, which develops bespoke software: [get in touch](https://api.whatsapp.com/send/?phone=34624237848&text=Hi+Dalmau%2C+I+come+from+the+PowerClock+page.+I+need%3A&type=phone_number&app_absent=0).

<!-- Pie: Portfolio · También he hecho: [Grabador de pantalla](/easy-screen-recorder) -->

---

# ANEXO A — JSON-LD

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SoftwareApplication",
      "@id": "https://solucionesconscientes.es/powerclock#app",
      "name": "PowerClock",
      "alternateName": "KShutdown Evolution",
      "description": "Programador de apagado, encendido y tareas para Linux: a una hora exacta o cuando se cumplen las condiciones que elijas.",
      "applicationCategory": "UtilitiesApplication",
      "operatingSystem": "Linux",
      "softwareVersion": "1.0.0",
      "datePublished": "2026-09-25",
      "dateModified": "2026-10-02",
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
      "isAccessibleForFree": true,
      "license": "https://www.gnu.org/licenses/gpl-3.0.html",
      "downloadUrl": "https://github.com/solucionesconscientes/powerclock/releases/latest/download/install-powerclock.sh",
      "screenshot": [
        "https://solucionesconscientes.es/img/powerclock/quick.webp",
        "https://solucionesconscientes.es/img/powerclock/rules.webp",
        "https://solucionesconscientes.es/img/powerclock/editor-when.webp"
      ],
      "author": {"@type": "Person", "name": "Dalmau Romaní", "url": "https://solucionesconscientes.es/sobre-mi"},
      "publisher": {"@type": "Organization", "name": "Soluciones Conscientes", "url": "https://solucionesconscientes.es"},
      "sameAs": [
        "https://github.com/solucionesconscientes/powerclock",
        "https://pypi.org/project/powerclock/"
      ]
    },
    {
      "@type": "BreadcrumbList",
      "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Portfolio", "item": "https://solucionesconscientes.es/portfolio"},
        {"@type": "ListItem", "position": 2, "name": "PowerClock", "item": "https://solucionesconscientes.es/powerclock"}
      ]
    }
  ]
}
```

El `FAQPage` se genera a partir de la sección de preguntas frecuentes (8 preguntas), con el texto exacto de la página.

# ANEXO B — Imágenes

| Archivo destino (`/img/powerclock/`) | Origen | Alt |
|---|---|---|
| `quick.webp` | repo `docs/images/es/quick.png` | Pestaña Rápido de PowerClock con la franja Próximo y una acción de apagado programada |
| `rules.webp` | `docs/images/es/rules.png` | Lista de reglas de PowerClock con cuándo actúa cada una |
| `editor-when.webp` | `docs/images/es/editor-when.png` | Editor de reglas con la regla escrita como una frase |
| `countdown.webp` | `docs/images/es/countdown.png` | Aviso de cuenta atrás antes de apagar, con Cancelar y Posponer |
| `history.webp` | `docs/images/es/history.png` | Historial de ejecuciones con resultado y ahorro estimado |
| `diagnostics.webp` | `docs/images/es/diagnostics.png` | Diagnóstico de compatibilidad del equipo |
| `og-powerclock.png` (1200×630) | **Generar**: icono + "PowerClock" + descriptor + captura Rápido | PowerClock, programador de apagado y encendido para Linux |
| `powerclock-demo.mp4` / `.webm` | **Lo graba Ser** (15–20 s): programar "Apagar dentro de 1 min" en Rápido → aparece en Programado → salta el aviso con el anillo → Cancelar | — |

# ANEXO C — A verificar antes de publicar

- La hoja de ruta del README (ES y EN) dice "**0.1 — Linux (ahora)**", pero el estado y PyPI dicen 1.0.0. Hay que corregirla en el README.
- En el portfolio no aparece PowerClock: añadir la tarjeta (Versión 1.0, octubre de 2026 · Python · PySide6 · GPL-3.0-or-later).
- `/powerclock` ya está enlazado desde los README publicados en PyPI: la URL tiene que existir y no cambiar.
- Revisar la ficha de PyPI: el `summary` está en inglés. Valorar añadir `Homepage = https://solucionesconscientes.es/powerclock` en `project.urls`.
