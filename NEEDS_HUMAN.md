# NEEDS_HUMAN — cosas que necesita Ser (con el comando exacto)

## 2026-11-04 · F4: probar en un navegador de verdad que el invitado descifra el HLS

**Qué falta.** El servidor está probado de punta a punta (desafío-respuesta HMAC, segmentos AES-128 servidos por el
túnel real, descifrado con la clave de la sala y rechazo con una clave incorrecta; ver `tmp/fase-share-4.md` y
`tmp/f4-captura/`). Lo que **no** se ha podido ejecutar es el navegador: en server1 Chrome cuelga en
`Page.navigate` desde F3, también contra un servidor local mínimo y con los tests que ya existían. Hace falta una
máquina con Chrome/Chromium funcional y node ≥ 22:

```bash
cd ~/Documentos/PROJECTES/productos/atalaya-player
uv run pytest tests/test_share_browser.py -q
```

Qué mirar: que la página entre sin mandar el token (pestaña Red: `api/challenge` y `api/join` con `proof`, ninguna
petición con `#k` ni a `.../mu-key`), que el vídeo se vea y que en la pestaña Red el segmento NO empiece por 0x47.
Si el loader de la clave falla, la página debe decir «No se pudo descifrar la retransmisión…».

**2. Subir la rama cuando la des por buena** (la regla del proyecto es que yo no empujo):

```bash
git push -u origin feat/share-internet
```

## 2026-10-15 · Los capítulos con una IA: la rama y una prueba que solo puedes hacer tú

**1. Pegar en una IA de verdad, una vez.** El flujo está probado de punta a punta con vídeos reales y con respuestas
de IA de ejemplo (los formatos que devuelven ChatGPT, Gemini y compañía están en los tests), pero **nadie ha pegado
todavía el texto en una IA real y ha traído la respuesta**. Es el único paso que necesita ojos y manos:

```bash
bin/mpv-uos ~/Vídeos/pelicula.mkv        # o el vídeo que quieras, mejor de más de 20 min
# MENU → Subtítulos → Capítulos con una IA → «Conseguir la transcripción»  (si el vídeo ya trae subtítulos, usa esos)
#                                          → «Copiar la transcripción para la IA» (ctrl+alt+c)
# Abre ChatGPT (o Gemini o Duck.ai), pega, copia su respuesta entera y vuelve al reproductor: ctrl+alt+p
```
Qué mirar: que los capítulos salgan en la barra con títulos que tengan sentido, que al cerrar y reabrir el vídeo
aparezcan solos, y que los tiempos caigan en el inicio de una frase (no a mitad). Si la IA devuelve los capítulos de
otro vídeo, el reproductor pregunta antes de aplicarlos: eso es lo que tiene que pasar.

**2. Subir la rama cuando la des por buena** (la regla del proyecto es que yo no empujo):

```bash
cd ~/Documentos/PROJECTES/productos/atalaya-player
git push -u origin feat/capitulos-ia
```

Los paquetes nuevos están en `dist/` (fuera de git, como siempre) por si los quieres instalar: el `.deb` de amd64 y el
`.AppImage` de x86_64 son los de esta máquina.

## 2026-10-09 · Subir la rama de la CI de Windows, y los paquetes para la máquina virtual

**1. La CI no existe hasta que la rama está en GitHub.** El workflow está escrito y probado todo lo que se puede
probar aquí (sintaxis con el PowerShell de verdad, nombres de las acciones del instalador contra el propio
script, la batería nueva recogida y omitida en Linux), pero **nunca se ha ejecutado**: eso solo pasa en GitHub.
La regla del proyecto es que yo no empujo, así que es tuyo:

```bash
cd ~/Documentos/PROJECTES/MPV-UOS
git push -u origin ci/windows-instalador
gh run watch   # o la pestaña Actions de github.com/solucionesconscientes/atalaya-player
```

Es gratis: en repositorio público los minutos de GitHub Actions no se cobran. Lo más probable que falle en la
primera pasada es el paso que instala mpv y ffmpeg (scoop, con chocolatey de reserva): si ahí se cae, el resto
del workflow no dice nada todavía.

**2. La pasada a mano con ratón** (SmartScreen, doble clic, menú Inicio, cortafuegos, winget) necesita un Windows
11 de escritorio, y para eso hay que instalar los paquetes de virtualización, que llevan sudo:

```bash
sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
                 virtinst virt-viewer virt-manager ovmf swtpm swtpm-tools
sudo adduser "$USER" libvirt        # cierra y abre la sesión después
```

El resto (ISO de evaluación, crear la máquina, instantánea limpia y la lista de comprobación) está en
`docs/PRUEBA-WINDOWS-VM.md`. `/dev/kvm` ya está; el equipo tiene 2 núcleos, así que Windows irá lento.

## 2026-10-05 · La máquina se ha quedado sin memoria, y 4,3 GiB son borrables
```bash
rm -rf /tmp/claude-1000/-home-pc-Documentos-PROJECTES-MPV-UOS/23df8650-f347-4cfa-8d39-77e419c85e41
```
`/tmp` es **tmpfs, o sea RAM**, y ahí quedaron 4,3 GiB de una sesión del 1 de octubre de este mismo proyecto: los
modelos `qwen2.5-3b-q4km.gguf` (1,8 G), `qwen2.5-1.5b-q4km.gguf` (1,1 G) y el gemma-3-1b, el binario de llama y un
`remux.webm` de 429 MB. Dos de esos modelos **ya están en `vendor/llm/models`**, que es donde les toca vivir según
las reglas del proyecto, y el de 3B no está porque se probó y se descartó: son copias de usar y tirar.

No lo borro yo porque está **fuera del proyecto** y ahí solo tengo lectura. Con eso, la máquina recupera ~4,3 GiB.
Mientras no se libere, **no se puede medir** la batería: con 1,7 GiB disponibles y el intercambio al 100 % un fallo
de test no dice nada del programa, y los tres de `test_share_browser` levantan un navegador de verdad. Lo que sí he
limpiado yo (dentro del proyecto): `tmp/`, de 6,0 GiB a 470 MiB, que libera **disco** (estabas al 95 %), no RAM.

Y un detalle del equipo, no del proyecto: 23 GiB de RAM con **512 MiB de intercambio** es muy poco colchón; cuando
se llena, no hay a dónde ir.

## 2026-10-05 · RESUELTO el 2026-10-06 · La web y el repositorio público (H50)
Las dos decisiones se tomaron en la sesión de esa noche, y lo que había detrás ya está hecho:

1. **El repositorio es público**: `github.com/solucionesconscientes/atalaya-player`, MIT. El nombre lleva sufijo
   porque `solucionesconscientes/atalaya` es del proyecto OSINT Atalaya, que queda aparcado. La rama por defecto
   es el tronco `nocturno/2026-09-28`; **`main` sigue con el andamiaje**, porque la regla del proyecto es no
   fusionar a `main`. Si quieres que el tronco sea `main` en el repositorio público, es tuyo y es una orden:
   ```bash
   git push origin nocturno/2026-09-28:main
   gh repo edit solucionesconscientes/atalaya-player --default-branch main
   ```
   Esto además desbloquea construir y probar los paquetes de Windows y macOS gratis en GitHub Actions.
2. **`/atalaya` se sirve en solucionesconscientes.es/atalaya**, que es un sitio en Astro desplegado por Cloudflare
   Pages desde la raíz del repositorio `websc`. La página está en vivo, con su tarjeta en `/portfolio`.
3. **Las capturas** salieron sin ti: `tools/capturas.sh` corre entero sobre una pantalla virtual
   (`xvfb-run -a -s "-screen 0 1400x900x24" tools/capturas.sh`). Si las quieres con una película de verdad en vez
   de con la carta de ajuste, sigue valiendo:
   ```bash
   tools/capturas.sh "/ruta/a/tu/pelicula.mkv"
   ```
   Ojo: el guion usa a propósito una carpeta de datos **vacía**. La primera tanda salió con tu historial de vídeos
   en el menú, con los títulos, camino de una página pública.

**Lo único que sigue esperando a ti** de todo esto es el **vídeo de cabecera** de la página, de 15-20 segundos:
elegir un canal → pasar a radio → abrir un vídeo de internet → crear una sala y copiar el enlace. No bloquea nada.

## 2026-10-04 · Los paquetes están hechos: lo que falta es abrirlos en otra máquina (H72)
Los cuatro de Linux, el de Windows y el camino de macOS están construidos y revisados por dentro. Lo que **yo no
puedo hacer** es ejecutarlos donde toca, porque aquí solo hay un x86-64 con Linux. Por orden de importancia:

1. **(HECHO el 2026-10-05)** Ser instaló el `.deb` y `sudo -n /usr/lib/mpv-uos/bin/wake check` contestó
   `rtcwake de util-linux 2.41.3`: **el despertador de las grabaciones programadas está resuelto** y la línea de
   `sudoers` a mano ya no hace falta, la pone el paquete. El único pero fue el del menú de aplicaciones: la entrada
   antigua del instalador por usuario tapa la del paquete (`~/.local/share/applications` manda sobre
   `/usr/share/applications`); se quita con `tools/install.sh --uninstall` y `tools/install.sh` ahora avisa antes.
   Lo que se hizo, para repetirlo en otra máquina:
   ```bash
   sudo apt install ./dist/atalaya-player_0.1.0_amd64.deb
   atalaya                                   # o desde el menú de aplicaciones
   sudo -n /usr/lib/mpv-uos/bin/wake check   # tiene que contestar la versión de rtcwake, sin pedir contraseña
   ```
   Si lo último funciona, **el despertador de las grabaciones programadas ya está resuelto** y la tarea de la
   línea de `sudoers` a mano se puede olvidar: la pone el paquete. Para quitarlo todo:
   `sudo apt remove --purge atalaya-player`.
2. **Una Raspberry (o cualquier ARM) con el `.deb` de arm64 o el AppImage de aarch64.** Están cruzados desde aquí
   y revisados por dentro (intérprete ARM de verdad, catálogos, runtime ARM), pero **nadie los ha ejecutado**:
   ```bash
   sudo apt install ./atalaya-player_0.1.0_arm64.deb     # o: chmod +x Atalaya-aarch64.AppImage && ./Atalaya-aarch64.AppImage
   ```
   Lo que más me interesa saber: si arranca, si mpvd se conecta, y qué dice al abrir una película (si el mpv de
   esa distribución es más viejo que 0.41, debería avisarte de lo que no va).
3. **Un Windows con el `.zip`**: descomprimir y **doble clic en `EMPEZAR-AQUI.cmd`**, que está arriba del todo.
   Ya no hay que instalar mpv a mano: ese arranque lo busca, lo instala con winget si falta y abre el reproductor
   (ADR-123). De esto no hay NADA probado en un Windows real: solo que el contenido, los lanzadores y el arranque
   son correctos, y que la cadena funciona con un PowerShell 7 en Linux. Dos cosas que me interesan de tu prueba:
   si Windows saca algún aviso antes de arrancar (y con qué texto exacto), y lo que conteste `winget search mpv`,
   para fijar el identificador del paquete en vez de depender del alias del catálogo.
4. **El correo del mantenedor del `.deb`.** Ahora pone `Atalaya Player <atalaya@solucionesconscientes.es>`, que me
   he inventado a partir de tu dominio porque no quería poner tu correo personal en un paquete que se distribuye.
   Dime cuál quieres y lo cambio (o `DEB_MAINTAINER="Nombre <correo>" tools/build_deb.sh`).

## 2026-10-03 · El mando del televisor en la Raspberry: ya está hecho, falta probarlo (H61)
**No hay que instalar nada**: el puente lee el aparato del kernel (`/dev/cec0`), no `cec-client`. En la Pi, con el
reproductor abierto, el mando de la tele debería manejarlo sin pedir permiso. Si no hace nada, en este orden:
```bash
ls -l /dev/cec*                      # en una Pi con el driver vc4 tiene que existir
groups | tr ' ' '\n' | grep -x video  # el aparato suele ser de root:video
sudo usermod -aG video "$USER"       # si no sales en el grupo (hay que volver a entrar en la sesión)
.venv/bin/python -m mpvd status      # «cec» tiene que salir como servicio disponible
```
Si el aparato existe, estás en el grupo y aun así no pasa nada, es que tu televisor no pasa las teclas: no todos lo
hacen, y algunos solo mientras somos la fuente activa. Para depurarlo, `sudo apt install cec-utils` y
`cec-client -m -d 8` enseña lo que llega; pásame esa salida. Mientras, funcionan el mando del móvil (`alt+z`) y un
gamepad por USB o Bluetooth en modo salón.

## 2026-10-03 · De las tres que quedan, qué haría yo (y qué no)
Ser preguntó por las tres cosas que solo puede hacer él. Esto es lo que recomiendo, por orden:

1. **`rtcwake`: sí, y es la única que tapa un agujero real.** (RESUELTO el 2026-10-05, lo pone el `.deb`.) Sin esa línea, una grabación o una reproducción
   programada a las 3:00 simplemente **no ocurre**: el equipo se suspende y no se despierta. El comando exacto está
   más abajo (sección del 2026-10-01); es una regla limitada a ese binario, no un sudo general.
2. **Puerto 8790/8791 en `ufw`: NO lo abriría.** Desde H51 la sala sale a internet **por defecto** por el túnel, que
   no necesita ningún puerto abierto, y compartir es justamente con quien no está en casa. Ese puerto solo sirve
   para invitados dentro de tu propio wifi, que es el caso secundario: no vale un agujero permanente en el
   cortafuegos. Si algún día el wifi importa, el comando sigue abajo y se abre entonces.
3. **Cuenta de OpenSubtitles: sí, pero sin prisa.** Son dos minutos tuyos y da subtítulos humanos al instante en
   películas conocidas. Todo lo demás ya lo cubre whisper sin cuenta ninguna, y encima mejor en material raro.

## 2026-09-28 · Notion (no bloquea)
- La ficha "MPV-UOS" (slug `mpv-uos`, https://app.notion.com/p/3e9d5e4ff9d5818989fddde90c3db7e1) quedó creada, pero el campo Stack solo admite
  opciones existentes (Python, uv, MCP): añadir Lua, Bash, mpv, ffmpeg, yt-dlp exigía modificar el esquema de la base y el permiso fue denegado.
  Si quieres esas etiquetas: añádelas a mano en Notion (Stack → editar opciones) o permite `mcp__claude_ai_Notion__notion-update-data-source`.
- El campo Repo está vacío porque el repo no tiene remoto. Cuando lo haya: `git remote add origin <url>` y actualizar la ficha.
- (hecho 2026-09-28 13:16) marca `~/.cache/notion-reg/mpv-uos` puesta desde la sesión interactiva; Notion al día hasta H2.

## 2026-09-28 · Opcional (no bloquea)
- (resuelto 2026-09-30) "Copiar URL" usa ahora el portapapeles nativo de mpv 0.41: ya no hace falta wl-clipboard.

## 2026-09-29 · Opcional (no bloquea)
- "Saltar intro/créditos" necesita `fpcalc` (Chromaprint). En este portátil ya está (`/usr/bin/fpcalc` 1.6.0); en otra máquina:
  `sudo apt install libchromaprint-tools`. Sin él la función se desactiva sola (capabilities.services.intro=false).

## 2026-09-29 · Mando QR/PWA (no bloquea los tests)
- `ufw` está activo con entrada DROP, así que el móvil no llegará al mando (puerto 8790) hasta abrirlo una vez:
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos remote'` (ajusta la subred a la de tu wifi).
  Prueba a mano: `bin/mpv-uos tests/fixtures/media/chapters.mkv`, `alt+z`, escanear el QR con el móvil (misma wifi).

## 2026-09-29 · Notion sin autorizar en la sesión nocturna (resuelto 2026-09-29: registrado desde la sesión interactiva)
- El conector "claude.ai Notion" pedía autenticación y la sesión no interactiva no puede hacer OAuth: la Bitácora no recoge H12–H13
  ni el cierre del proyecto. Autorízalo en claude.ai → Ajustes → Conectores y ejecuta `/registrar` en una sesión interactiva.
- Entradas pendientes para `/registrar` (Bitácora, Fuente "Code CLI", Referencia `pc-latitude5480` + commit, Vigente):
  - `mpv-uos|2026-09-29|avance|h12-mando-qr-pwa` — commit a8532cb: mando desde el móvil (QR con alt+z, PWA servida por mpvd, cookie HMAC).
  - `mpv-uos|2026-09-29|decision|mando-qr-sin-dependencias` — ADR-032: HTTP/SSE y QR propios en Python, overlay ASS, HTTP solo en LAN.
  - `mpv-uos|2026-09-29|avance|h13-cierre` — commit 59b6c2c: tools/install.sh, docs/USO.md, README y PLATAFORMAS; backlog H0–H13 completo.
  - `mpv-uos|2026-09-29|problema|puerto-mando-ufw` — ufw bloquea el 8790; comando arriba.
  Ficha: Estado → backlog completado; Próximo paso → el "SIGUIENTE PASO" de PROGRESS.md. Después: `touch ~/.cache/notion-reg/mpv-uos`.

## 2026-09-30 · Tras las pruebas de Ser (H14)
- **Mando por QR**: sigue haciendo falta abrir el puerto una vez (el QR y el menú del mando ya muestran esta orden con tu subred):
  `sudo ufw allow from 192.168.1.0/24 to any port 8790 proto tcp comment 'mpv-uos mando'`
- **Carpeta oculta de la versión anterior**: la detección de intro escribía `.mpv-uos/segments.json` junto a los vídeos; ya no lo hace.
  Queda una en `~/Descargas/Los tres días del cóndor (1975)/.mpv-uos`. Para quitarla:
  `rm -r "$HOME/Descargas/Los tres días del cóndor (1975)/.mpv-uos"`
## 2026-11-04 · Registro del proyecto (no bloquea)
- Las sesiones desatendidas de F1 y F2 no tenían MCP de Notion ni permiso para el MCP `vault`, así que el registro
  en «Centro de proyectos» y en la memoria compartida queda pendiente. Los partes de las fases están en
  `tmp/fase-share-1.md` y `tmp/fase-share-2.md` (ignorados por git).
## 2026-09-30 · Compartir (H25)
- **Túnel a internet (H25 punto 3) — HECHO el 2026-10-01** (ADR-068), ya no necesita nada de ti salvo encenderlo:
  *Compartir → Que se pueda entrar desde internet* (encendido por defecto, se recuerda). Desde F1 (ADR-139, 2026-11-04)
  **no hay que instalar nada a mano**: si el paquete no lo lleva dentro, mpvd descarga `cloudflared`, comprueba su
  SHA-256 y lo guarda la primera vez que abres una sala hacia internet. Probado con un túnel real: la dirección
  pública sirve lo que sirve el reproductor y el proceso muere con la sala.
- **Puerto de las salas**: solo hace falta si NO usas el túnel (dentro de tu wifi). `ufw` bloquea la entrada; para que
  otros equipos de tu red entren en una sala:
  `sudo ufw allow from 192.168.1.0/24 to any port 8791 proto tcp comment 'mpv-uos compartir'`

## 2026-09-30 · Plataformas (H28, no bloquea)
- **ARM64 / Raspberry Pi 5** (no hay hardware aquí). En la Pi (Raspberry Pi OS 64 bits, mpv ≥ 0.41):
  `git clone … && cd MPV-UOS && curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync --extra desktop &&
  tools/vendor.sh && tools/check.sh` y prueba el modo salón: `bin/mpv-uos` → `alt+m` → Preferencias → Modo salón.
  Si falla algo, guarda `tmp/check.log` y el `.cache/mpvd.log`.
- **AppImage en otra distribución**: `tools/build_appimage.sh` y abre `dist/Atalaya-x86_64.AppImage` en un equipo con
  otra distro (Fedora, Arch) que tenga mpv ≥ 0.41: debería abrir la pantalla de inicio y `alt+t` la TV.
- **macOS**: en un Mac con `brew install mpv uv`: clona, `uv sync --extra desktop && tools/vendor.sh &&
  tools/build_macos_app.sh` y abre `dist/MPV-UOS.app` (la primera vez: clic derecho → Abrir, no está firmado).
- **Windows** (no hay Windows aquí; los scripts solo se han ejecutado con pwsh 7 en Linux). En un Windows 10/11 con mpv ≥ 0.41,
  ffmpeg, uv y git en el PATH: clona el repo y, en PowerShell,
  `powershell -NoProfile -ExecutionPolicy Bypass -File tools\install.ps1 -Whisper` y luego `mpv-uos` (o menú Inicio → MPV-UOS):
  debería abrir la pantalla de inicio; `alt+t` la TV, un vídeo de YouTube con `ctrl+u`. Si falla, guarda la salida del
  instalador y `.cache\mpvd.log` del checkout (y la salida de `.venv\Scripts\python -m mpvd status`).

## 2026-10-01 · El despertador para grabar necesita UNA orden con sudo (H40/F4)
**RESUELTO el 2026-10-05 por el `.deb`**: instala `/etc/sudoers.d/mpv-uos-rtcwake` limitado a su propio
ayudante `/usr/lib/mpv-uos/bin/wake`, y Ser comprobó que contesta sin contraseña. Lo de abajo solo hace
falta si usas el repositorio en vez del paquete.
Suspender y apagar el equipo ya funcionan sin contraseña (logind contesta `yes` a `CanSuspend` y `CanPowerOff`), pero
**poner el despertador** necesita `rtcwake`, y `rtcwake` necesita root: comprobado aquí el 2026-10-01, da
«/dev/rtc0: Permiso denegado» y `sudo -n` pide contraseña. Con esto, una sola vez, el reproductor puede despertar el
equipo 5 min antes de una grabación programada:
```bash
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/rtcwake" | sudo tee /etc/sudoers.d/mpv-uos-rtcwake \
  && sudo chmod 0440 /etc/sudoers.d/mpv-uos-rtcwake
```
Comprobarlo después: `sudo -n rtcwake --version` (tiene que contestar sin pedir nada) y, en el reproductor,
*TV y radio → Grabaciones programadas*: la fila «Despertar el equipo 5 min antes» dejará de decir que falta algo.
Para quitarlo: `sudo rm /etc/sudoers.d/mpv-uos-rtcwake`. Es una regla **limitada a ese programa**, no un sudo general.
En macOS es lo mismo con `/usr/bin/pmset`. Mientras no esté puesto, **no se pierde ninguna grabación**: se programa
igual (si el equipo está encendido graba) y la grabación anota «sin despertador: …».

Aviso de lo que el despertador NO hace: solo despierta de la **suspensión**. Un equipo apagado del todo no se puede
encender desde el programa (eso es cosa de la BIOS/UEFI: «Wake on RTC alarm»).

## 2026-10-01 · Tu llama-server (resuelto, nada que hacer)
Me pediste pausarlo para dejar CPU libre y lo pausé (`kill -STOP`). A las 13:39 ya estaba otra vez en marcha (no lo
reanudé yo), así que no hay nada pendiente aquí. Si alguna vez lo ves parado: `kill -CONT <pid>`.

## 2026-10-01 · Subtítulos de internet (H36/C5, no bloquea)
- **Subdl descartado** (2026-10-02, decisión de Ser): ya hay dos proveedores (la web del vídeo y
  OpenSubtitles) y el tercero obligaba a escribir un cliente contra una API que no se puede comprobar
  sin clave. No hace falta que consigas nada.
- **Podnapisi queda descartado**: su dominio ya no existe. Comprobado el 2026-10-01 con el resolutor del sistema y con
  1.1.1.1: `www.podnapisi.net` y `podnapisi.net` dan NXDOMAIN. No es un corte momentáneo: no hay a dónde conectarse.
- **Cuenta de OpenSubtitles (usuario y contraseña), no solo la Api-Key**: la clave sola basta para BUSCAR, pero las
  descargas anónimas del API antiguo (`api.opensubtitles.org/xml-rpc`) devuelven un fichero de 102 bytes con un anuncio
  en vez del subtítulo (comprobado el 2026-10-01 con cuatro subtítulos distintos de *Interstellar*: `SubSize` decía
  ~155 KB y llegaban 102 B). O sea: para descargar de verdad hace falta cuenta. Ponla en *Biblioteca → Ajustes →
  Usuario / Contraseña de OpenSubtitles*; con ella el menú enseña además el cupo que te queda (200 al día por IP en el
  API antiguo, 100 al día con cuenta gratuita en el nuevo).

## 2026-10-28 · Windows: probar el instalador con el intérprete en `runtime\` (H75, no bloquea)
El .exe y el .zip ya no llevan `.venv\Scripts\python.exe` (965 ficheros de un CPython completo con nombre de venv y
sin `pyvenv.cfg`): ahora el intérprete va en `runtime\python.exe` y el instalador cierra por ruta y por nombre antes de
borrar. Falta verlo en un Windows de verdad:
1. Con la versión anterior **abierta**, ejecutar `Atalaya-0.1.0-setup.exe`: tiene que cerrarla, sustituir `runtime\`
   (y borrar el `.venv` viejo si lo hay) y terminar **sin** «Error abriendo archivo para escritura».
2. Con el programa abierto, ejecutar el instalador otra vez: mismo resultado (cierra y reinstala).
3. En una instalación nueva, comprobar que existe `%LOCALAPPDATA%\Programs\Atalaya\runtime\python.exe`, que **no** hay
   `.venv`, y que TV y radio conectan (mpvd arranca con ese Python).
4. Probar `⌫`, `Esc`, el botón «atrás» del ratón y el botón ⌫ de la barra con TV y radio: ⌫ abre el panel del canal,
   ⌫/Esc lo cierran sin cambiar de canal y `Esc` no cierra el reproductor.
Si algo falla, el instalador deja el motivo en `%LOCALAPPDATA%\Atalaya\instalador.log` y el lanzador en
`lanzador.log`; adjuntar los dos.

## 2026-10-28 · Notion sin conector en la sesión (no bloquea)
Esta sesión no tenía MCP de Notion, así que el avance de ADR-136/ADR-137 (panel de canales, ⌫/Esc y el intérprete de
Windows en `runtime\`) no se ha registrado en la Bitácora. Se puede registrar desde una sesión con el conector.

## 2026-11-04 · Tests de navegador de la sala (F3, no bloquea)
En server1 los tests de navegador **no se pueden ejecutar**: Chrome cuelga en `Page.navigate` incluso contra un
servidor local mínimo, y también con los tests que ya existían antes de F3 (`test_share_browser.py`). No es cosa del
cambio de transporte. El test nuevo de sondeo y reconexión (`?poll=1`, cortar las peticiones y volver) está escrito
y pasa `node --check`; falta verlo en una máquina con Chrome/Chromium funcional y node ≥ 22:

```bash
cd ~/Documentos/PROJECTES/productos/atalaya-player
uv run pytest tests/test_share_browser.py -q
```

Mientras tanto, el camino de servidor del sondeo está cubierto en `test_share_http.py` y el de consola por el túnel
real en `tmp/fase-share-3-medicion.md` (20/20 cambios, p95 del total 1031 ms).

## 2026-11-04 · Notion y vault en la sesión de F3 (no bloquea)
La sesión no tiene conector de Notion y el MCP del vault pidió permiso y no se concedió (trabajo desatendido). El
avance de F3 (sondeo por el túnel, ADR-141) queda en `PROGRESS.md`, `BACKLOG.md`, `docs/DECISIONS.md` y
`tmp/fase-share-3.md`; falta pasarlo a Notion y a `proyectos/` del vault desde una sesión con permiso.

## 2026-11-04 · Notion y vault en la sesión de F4 (no bloquea)
Igual que en F3: no hay conector de Notion y el MCP del vault pidió permiso y no se concedió (trabajo desatendido).
El avance de F4 (desafío HMAC, sesión corta, AES-128 del relay, ADR-142) queda en `PROGRESS.md`, `BACKLOG.md`,
`docs/DECISIONS.md`, `tmp/fase-share-4.md` y `tmp/share-internet-RESUMEN.md`; falta pasarlo a Notion y a
`proyectos/` del vault desde una sesión con permiso.

## 2026-11-05 · Chrome en server1: no era Chrome, era D-Bus (resuelto, no bloquea)
Los tests de navegador (`tests/test_share_browser.py`) y Chrome/Edge headless colgaban en `Page.navigate` incluso
contra un servidor local mínimo. La causa es la sesión D-Bus de esta máquina: Chromium se queda esperando en el
arranque/red. Con `DBUS_SESSION_BUS_ADDRESS=/dev/null DBUS_SYSTEM_BUS_ADDRESS=/dev/null` cargan y la batería de
navegador pasa entera. No hace falta intervención humana; se deja anotado por si vuelve a aparecer.

## 2026-11-05 · Notion y vault en la sesión de F5 (no bloquea)
La sesión no tiene conector de Notion, y el MCP del vault pidió permiso y no se concedió (trabajo desatendido). El
avance de F5 (ADR-143, medición de los cuatro criterios y la batería en verde) queda en `PROGRESS.md`,
`docs/DECISIONS.md` y `informes/atalaya-f5-2026-11-05/f5-sincronia.json`; falta pasarlo a la Bitácora y a
`proyectos/` del vault desde una sesión con permiso.

## 2026-11-05 · `tools/check.sh` completo no es ejecutable en server1 (no bloquea F5)
El paso de lint Lua marca error porque no hay `luacheck` ni `luajit` instalados (y no se pueden instalar sin
sudo). Los tests de ASR fallan en este entorno antes de llegar a `share`; no se ha tocado nada de ASR. La batería
seleccionada de `share` + `mu-share` sí pasa (111 pasados, 3 omitidos, 0 rojos). Para el próximo cierre, ejecutar
`tools/check.sh` en una máquina con las herramientas de Lua y el entorno de ASR completo.
