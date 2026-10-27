# Probar Atalaya Player en un Windows de verdad (máquina virtual)

La CI de Windows (`.github/workflows/windows.yml`) ejecuta el instalador en un Windows Server 2025 real, con las
dos shells, el registro, el menú Inicio y las descargas verificadas. Lo que no puede hacer es **usar el ratón**, y
ahí queda todo lo que más asusta a quien se descarga el programa: el aviso de SmartScreen, el doble clic en
`EMPEZAR-AQUI.cmd`, que el acceso del menú Inicio abra el reproductor, que un enlace `mpv-uos://` del navegador
funcione, el diálogo del cortafuegos del mando y que el vídeo se vea. Esta es la pasada a mano, una vez, en un
Windows 11 de escritorio; después se anota el resultado en `docs/PLATAFORMAS.md`.

Además, Windows Server 2025 **no trae winget**, así que la cadena «no hay mpv → lo instalo con winget → arranco»
de `bin/empezar.ps1` (ADR-123), que es justo la que va a usar una persona, solo se puede comprobar aquí.

## 1. Lo que hay que instalar en el equipo (una vez, con sudo)

Está en `NEEDS_HUMAN.md` porque las reglas del proyecto no me dejan instalar paquetes del sistema. En Debian 13:

```bash
sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
                 virtinst virt-viewer virt-manager ovmf swtpm swtpm-tools
sudo adduser "$USER" libvirt        # cierra y abre la sesión después
```

`/dev/kvm` ya está en este equipo, así que la virtualización va con aceleración. Cuidado con una cosa: el equipo
tiene **2 núcleos y 15 GiB**, de los que suelen quedar libres unos 10. La máquina de abajo pide 2 núcleos y 6 GiB;
Windows 11 irá lento pero sobrado para pulsar botones. Cierra el navegador y lo demás mientras la uses.

## 2. La ISO de evaluación (gratis, 90 días, sin clave)

Microsoft publica **Windows 11 Enterprise** de evaluación en el Evaluation Center. Hay que registrarse con un
correo, y la propia página dice que no hace falta clave de producto:

<https://www.microsoft.com/es-es/evalcenter/download-windows-11-enterprise>

```bash
mkdir -p ~/vm/iso ~/vm/discos
# guarda la ISO como ~/vm/iso/windows11-enterprise-eval.iso (unos 6 GB)
```

Si la evaluación caduca sin activar, el fondo se vuelve negro y el equipo se apaga cada hora: para probar durante
una tarde da igual, y siempre se puede volver a la instantánea limpia (paso 5) o rehacer la máquina.

## 3. Crear la máquina

En la sesión del usuario (`qemu:///session`), sin root. **SATA para el disco y `e1000e` para la red** a propósito:
así Windows tiene controladores desde el primer arranque y no hay que meterle el ISO de virtio para que vea el
disco o salga a internet. El TPM 2.0 emulado y el arranque UEFI son requisitos de Windows 11, no un adorno.

```bash
virt-install \
  --connect qemu:///session \
  --name atalaya-win11 \
  --osinfo win11 \
  --memory 6144 --vcpus 2 --cpu host-passthrough \
  --machine q35 --features smm.state=on --boot uefi \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --disk path="$HOME/vm/discos/atalaya-win11.qcow2",size=64,format=qcow2,bus=sata \
  --cdrom "$HOME/vm/iso/windows11-enterprise-eval.iso" \
  --network user,model=e1000e \
  --graphics spice --video qxl --sound none
```

Para volver a la ventana cuando la cierres:

```bash
virt-viewer --connect qemu:///session atalaya-win11
virsh --connect qemu:///session start atalaya-win11
```

Si la instalación se queja de **Arranque seguro**, es que libvirt eligió un OVMF sin esa capacidad. Mira qué hay
(`ls /usr/share/OVMF/`) y sustituye `--boot uefi` por el firmware explícito, que en Debian suele ser:

```
  --boot loader=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd,loader.readonly=yes,loader.type=pflash,loader.secure=yes,nvram.template=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
```

## 4. La cuenta, durante la instalación

Microsoft ha ido cerrando los atajos para crear una cuenta local: `oobe\bypassnro` y `ms-cxh:localonly` ya **no
funcionan** en las compilaciones nuevas de Windows 11. Opciones, de más cómoda a menos:

1. La ISO es **Enterprise**: en la pantalla de la cuenta, elige «Configurar para una organización» y luego
   «Unirse a un dominio en su lugar» → te deja crear un usuario local. Es lo primero que hay que intentar.
2. Si no aparece, inicia sesión con una cuenta de Microsoft cualquiera: es una máquina de usar y tirar.
3. La vía reproducible de verdad es un fichero de respuestas (`autounattend.xml`) en un segundo ISO. Solo merece
   la pena si vas a rehacer la máquina muchas veces.

## 5. Instantánea limpia (hazla antes de tocar nada)

Lo que de verdad queremos probar es **la primera vez** que alguien instala esto, y eso solo pasa una vez por
Windows. Con una instantánea del Windows recién hecho, cada prueba vuelve a ser la primera:

```bash
virsh --connect qemu:///session snapshot-create-as atalaya-win11 limpio "Windows 11 recién instalado"
virsh --connect qemu:///session snapshot-revert    atalaya-win11 limpio      # volver a empezar
virsh --connect qemu:///session snapshot-list      atalaya-win11
```

## 6. Meter el programa como lo haría una persona: descargándolo

**Esto importa**: SmartScreen solo salta si el fichero llega *marcado como descargado de internet* (la «marca de
la web»). Si lo pasas por una carpeta compartida, no sale el aviso y te quedas sin probar justo lo que decidimos
evitar con el `.zip` (ADR-117). Así que sírvelo por HTTP desde el equipo y descárgalo con el navegador de la
máquina virtual:

```bash
tools/build_zip_windows.sh            # deja dist/Atalaya-<versión>-windows-x86_64.zip
cd dist && python3 -m http.server 8000
```

Con `--network user` la red de la máquina virtual es NAT de QEMU, y el equipo se ve desde dentro como
**10.0.2.2**: en el Edge de la máquina virtual abre `http://10.0.2.2:8000/` y descarga el `.zip`.

## 7. La lista de comprobación (esto es lo que la CI no puede)

Marca lo que vaya bien y apunta tal cual lo que vaya mal: el valor está en lo que falle.

**Primer contacto (desde la instantánea limpia, sin mpv instalado)**
- [ ] Al descargar el `.zip`, el navegador no lo bloquea (o se puede conservar en un clic).
- [ ] Descomprimir con el Explorador funciona y `EMPEZAR-AQUI.cmd` queda arriba, a la vista.
- [ ] Doble clic en `EMPEZAR-AQUI.cmd`: ¿sale SmartScreen? ¿Qué dice exactamente y cuántos clics cuesta seguir?
- [ ] Sin mpv: ofrece instalarlo con winget, lo instala y **arranca el reproductor en la misma sesión** (esto es
      el motivo de ser de `empezar.ps1`: una consola que acaba de instalar algo con winget no ve el PATH nuevo).
- [ ] Se ve el vídeo, se oye, y el menú de uosc se abre con el botón derecho.
- [ ] Arrastrar un vídeo encima de `EMPEZAR-AQUI.cmd` lo reproduce.

**El instalador (`tools\install.ps1`, dentro de la carpeta)**
- [ ] `powershell -NoProfile -ExecutionPolicy Bypass -File tools\install.ps1 -NoSync` termina sin errores.
- [ ] Aparece **Atalaya Player** en el menú Inicio, con su icono, y al pulsarlo abre el reproductor.
- [ ] No parpadea una consola negra al abrirlo (es lo que busca `-WindowStyle Hidden` + `WindowStyle = 7`).
- [ ] Un enlace `mpv-uos://open?path=...` desde el navegador abre el reproductor (y si falla, el error sale en una
      ventana, no en una consola que no existe: `-Gui`).
- [ ] `tools\install.ps1 -Uninstall` deja el menú Inicio limpio y no se lleva los datos (`%APPDATA%\mpv-uos`).

**Lo gordo del día a día**
- [ ] Mando QR / «ver juntos»: sale el diálogo del **Cortafuegos de Windows** y, al permitirlo, el móvil entra.
- [ ] Una película con acentos y espacios en el nombre, y otra en una ruta larga.
- [ ] Subtítulos IA (`install.ps1 -Whisper`) sobre un trozo corto: que whisper-cli.exe arranque de verdad.
- [ ] Descargar de YouTube (`yt-dlp.exe` + deno) y «Enviar a Atalaya» desde el navegador.
- [ ] Decodificación por hardware: **aquí no se puede** (no hay GPU en la máquina virtual). Queda para un Windows
      físico; mira `dxdiag` y la etiqueta de códec que muestra el reproductor.

## 8. Apuntar el resultado

Lo que pase aquí es lo único que convierte un «debería ir» en un «probado». Al terminar:

- Cambia en `docs/PLATAFORMAS.md` las casillas de Windows que hayan pasado, con la fecha y la versión de Windows.
- Si algo falla, abre la tarea en `BACKLOG.md` con el síntoma exacto y, si hace falta, una entrada en
  `NEEDS_HUMAN.md`. Un fallo que solo se ve con ratón es exactamente lo que no iba a aparecer en la CI.

## 9. TV y radio: listas y canales (Windows)

Con el `.zip` actualizado, desde su carpeta:

```powershell
.venv\Scripts\python.exe tools\verificar-iptv.py --limite 3 --tiempo 20
```

El script descarga las tres listas, las cuenta y abre una muestra de cada una con el `mpv` de Windows. Lo que
importa es separar dos cosas: si **la lista no carga** (red, proxy, TLS) o si **un canal concreto no responde**
(geobloqueo, emisión caída). Para ver solo las listas, `--solo-listas`; para dejar el informe, `--salida informe.json`.

En el uso normal, `alt+t` abre *España TV* (TDTChannels), *España radio* y *Mundo* (iptv-org sin España). Mientras
ves un canal, `alt+shift+t` abre directamente su lista; el canal actual sale marcado. Si un canal falla, `Tab` →
*Comprobar canales en segundo plano* dice cuáles responden y con qué calidad.
