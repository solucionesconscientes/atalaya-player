#!/usr/bin/env bash
# Construye dist/<paquete>_<versión>_<arch>.deb (H72, ADR-117): los ficheros del proyecto que lleva git, un
# CPython 3.12 reubicable (el de python-build-standalone que gestiona uv) con los extras ligeros, el yt-dlp
# vendorizado y la regla de sudoers de `rtcwake`, que es lo único que el .deb puede hacer y el AppImage no.
#
# mpv NO va dentro: se usa el del sistema (ADR-067 lo decidió y H71 lo confirmó midiendo; meterlo dentro rompe la
# aceleración por hardware, que depende de los drivers de la máquina). Por eso `Depends: mpv` SIN versión mínima:
# en Debian 13 y Raspberry Pi OS el del sistema puede ser más viejo que 0.41, y es mejor instalarse y avisar al
# arrancar de lo que no va que negarse a instalar.
#
# Uso: tools/build_deb.sh [--arch amd64|arm64] [--extras desktop,impersonate] [--out DIR]
# Necesita: git, uv, dpkg-deb, fakeroot, curl, sha256sum (y red la primera vez que se cruza a arm64).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/vendor.lock"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
EXTRAS="desktop"
OUT="$ROOT/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    --extras) EXTRAS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
case "$ARCH" in
  amd64) PY_TRIPLE="x86_64-unknown-linux-gnu" ;;
  arm64) PY_TRIPLE="aarch64-unknown-linux-gnu" ;;
  *) echo "arquitectura no contemplada: $ARCH (amd64 o arm64)" >&2; exit 2 ;;
esac
for cmd in git uv dpkg-deb fakeroot curl sha256sum; do
  command -v "$cmd" >/dev/null || { echo "falta $cmd" >&2; exit 1; }
done

VERSION="$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' \
           "$ROOT/pyproject.toml")"
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/brand.json")"
APP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id","mpv-uos"))' "$ROOT/brand.json")"
SITE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("site",""))' "$ROOT/brand.json")"
PKG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"].lower().replace(" ","-"))' \
       "$ROOT/brand.json")"
# El correo del mantenedor NO se inventa ni se saca de la cuenta de nadie: hasta que Ser diga el suyo va el del
# dominio del proyecto, y está anotado en NEEDS_HUMAN.md como lo único que hay que cambiar antes de publicar.
MAINTAINER="${DEB_MAINTAINER:-$NAME <atalaya@solucionesconscientes.es>}"

WORK="$ROOT/tmp/deb-$ARCH"
PKGDIR="$WORK/pkg"
APP="$PKGDIR/usr/lib/$APP_ID"
rm -rf "$WORK"
mkdir -p "$APP" "$PKGDIR/DEBIAN" "$PKGDIR/usr/bin" "$PKGDIR/usr/share/applications" \
         "$PKGDIR/usr/share/icons/hicolor/512x512/apps" "$PKGDIR/usr/share/doc/$PKG" "$PKGDIR/etc/sudoers.d"

# 1. la aplicación: exactamente lo que lleva git (ni .venv, ni cachés, ni descargas de vendor, ni tests)
# OJO: `locales` tiene que estar. Sin los catálogos todo cae al castellano sin avisar, porque ese respaldo
# silencioso es justo lo que se diseñó (H49, ADR-087) — y el AppImage se publicó una vez sin ellos.
git -C "$ROOT" ls-files -z -- bin mpv-config mpvd locales brand.json pyproject.toml vendor.lock README.md \
    docs/USO.md docs/ATAJOS.md docs/marca | (cd "$ROOT" && xargs -0 cp --parents -t "$APP")
chmod 0755 "$APP/bin/mpv-uos" "$APP/bin/wake"
if [ -d "$ROOT/mpv-config/scripts/uosc/bin" ]; then
  cp -a "$ROOT/mpv-config/scripts/uosc/bin" "$APP/mpv-config/scripts/uosc/"
  # los ayudantes de uosc para macOS y Windows van en el paquete (uosc los busca por nombre) pero aquí no se
  # ejecutan: sin el bit de ejecución, para que ningún revisor los tome por programas de este sistema
  chmod 0644 "$APP/mpv-config/scripts/uosc/bin/"*darwin* "$APP/mpv-config/scripts/uosc/bin/"*.exe 2>/dev/null || true
fi
mkdir -p "$APP/vendor/bin"
if [ -f "$ROOT/vendor/bin/yt-dlp" ] && [ "$ARCH" = "$(dpkg --print-architecture)" ]; then
  cp "$ROOT/vendor/bin/yt-dlp" "$APP/vendor/bin/"
  [ -f "$ROOT/vendor/bin/yt-dlp.version" ] && cp "$ROOT/vendor/bin/yt-dlp.version" "$APP/vendor/bin/"
fi

# 2. el intérprete, reubicable. Para la arquitectura de la máquina se copia el que gestiona uv; para la otra se
# baja el tarball de python-build-standalone fijado en vendor.lock y se comprueba su SHA-256.
url_var="PBS_${ARCH^^}_URL"; sha_var="PBS_${ARCH^^}_SHA256"
url="${!url_var:-}"; sha="${!sha_var:-}"
[ -n "$url" ] || { echo "falta $url_var en vendor.lock" >&2; exit 1; }
tarball="$ROOT/vendor/cpython-$ARCH.tar.gz"
if [ ! -f "$tarball" ]; then
  mkdir -p "$ROOT/vendor"
  curl -fsSL -o "$tarball.part" "$url"
  echo "$sha  $tarball.part" | sha256sum -c --quiet
  mv "$tarball.part" "$tarball"
fi
mkdir -p "$WORK/py"
tar -xzf "$tarball" -C "$WORK/py"      # el tarball «install_only» trae python/ con bin, lib e include
cp -a "$WORK/py/python" "$APP/python"
rm -rf "$APP/python/lib/python3.12/test" "$APP/python/lib/python3.12/idlelib" \
       "$APP/python/lib/python3.12/tkinter" "$APP/python/lib/python3.12/turtledemo"
find "$APP/python" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Limpieza del intérprete: lo que no se usa, fuera. Tcl/Tk (que arrastra un RPATH a /tools/deps/lib de la máquina
# donde se compiló), pip, idle y 2to3; y los .py de la biblioteca estándar no son programas aunque lleven shebang,
# así que se les quita el bit de ejecución —si no, cualquier revisor de paquetes los cuenta como scripts sueltos
# que necesitarían depender de python3, que es justo lo que este paquete evita llevándose el suyo—.
rm -rf "$APP/python"/lib/libtcl*.so "$APP/python"/lib/libtk*.so "$APP/python"/lib/itcl* "$APP/python"/lib/tdbc*
rm -rf "$APP/python"/lib/tcl8* "$APP/python"/lib/tk8* "$APP/python"/lib/thread* "$APP/python"/lib/tcl* "$APP/python"/lib/sqlite3*
rm -f "$APP/python"/lib/python3.12/lib-dynload/_tkinter*.so "$APP/python"/bin/pydoc3* "$APP/python"/bin/python3.12-config
rm -rf "$APP/python"/lib/python3.12/config-3.12-*
rm -f "$APP/python"/bin/pip "$APP/python"/bin/pip3 "$APP/python"/bin/pip3.12 "$APP/python"/bin/idle3 "$APP/python"/bin/idle3.12 \
      "$APP/python"/bin/2to3 "$APP/python"/bin/2to3-3.12
find "$APP/python"/lib -name '*.py' -type f -exec chmod 0644 {} +
find "$APP/python"/lib -name '*.so*' -type f -exec chmod 0644 {} +


# los extras ligeros, en la plataforma del paquete (solo ruedas: cruzar no puede compilar nada)
pkgs=()
IFS=',' read -r -a wanted <<<"$EXTRAS"
for extra in "${wanted[@]}"; do
  [ -n "$extra" ] || continue
  while IFS= read -r dep; do [ -n "$dep" ] && pkgs+=("$dep"); done < <(
    python3 - "$ROOT/pyproject.toml" "$extra" <<'PYEOF'
import sys, tomllib
data = tomllib.load(open(sys.argv[1], "rb"))
for dep in data["project"]["optional-dependencies"].get(sys.argv[2], []):
    print(dep)
PYEOF
  )
done
if [ "${#pkgs[@]}" -gt 0 ]; then
  if [ "$ARCH" = "$(dpkg --print-architecture)" ]; then
    uv pip install --quiet --python "$APP/python/bin/python3.12" --break-system-packages "${pkgs[@]}"
  else
    uv pip install --quiet --python-platform "$PY_TRIPLE" --python-version 3.12 --only-binary :all: \
      --target "$APP/python/lib/python3.12/site-packages" "${pkgs[@]}"
  fi
fi
mkdir -p "$APP/.venv/bin"
ln -sf ../../python/bin/python3.12 "$APP/.venv/bin/python"

# 3. el lanzador: lo mismo que hace el AppRun del AppImage, porque el programa instalado tampoco puede escribir
# donde vive. Dos nombres: el de la marca y el de siempre (los documentos y los atajos usan mpv-uos).
cat > "$PKGDIR/usr/bin/$APP_ID" <<EOF
#!/bin/sh
# $NAME, instalado por paquete: la aplicación es de solo lectura, así que cachés, datos y la actualización diaria
# de yt-dlp van a las carpetas de la persona que lo usa.
APP="/usr/lib/$APP_ID"
DATA="\${MPV_UOS_DATA_DIR:-\${XDG_DATA_HOME:-\$HOME/.local/share}/$APP_ID}"
export MPV_UOS_CACHE_DIR="\${MPV_UOS_CACHE_DIR:-\${XDG_CACHE_HOME:-\$HOME/.cache}/$APP_ID}"
export MPV_UOS_VENDOR_BIN="\${MPV_UOS_VENDOR_BIN:-\$DATA/bin}"
export MPV_UOS_PACKAGED=deb
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "\$MPV_UOS_VENDOR_BIN"
if [ ! -f "\$MPV_UOS_VENDOR_BIN/yt-dlp" ] && [ -f "\$APP/vendor/bin/yt-dlp" ]; then
  cp "\$APP/vendor/bin/yt-dlp" "\$MPV_UOS_VENDOR_BIN/yt-dlp" && chmod +x "\$MPV_UOS_VENDOR_BIN/yt-dlp"
fi
if [ -z "\${MPV_UOS_MPV:-}" ] && ! command -v mpv >/dev/null 2>&1; then
  text="$NAME necesita el reproductor mpv. Instálalo con: sudo apt install mpv"
  command -v notify-send >/dev/null 2>&1 && notify-send -a "$NAME" "Falta mpv" "\$text"
  echo "\$text" >&2
  exit 1
fi
exec "\$APP/bin/mpv-uos" "\$@"
EOF
chmod +x "$PKGDIR/usr/bin/$APP_ID"
ln -s "$APP_ID" "$PKGDIR/usr/bin/${PKG%%-*}"

# 4. escritorio, icono y documentación
cat > "$PKGDIR/usr/share/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
GenericName=Reproductor multimedia
Comment=Vídeo, TV y radio, YouTube, subtítulos con IA
Exec=$APP_ID %U
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Video;Player;TV;
MimeType=video/mp4;video/x-matroska;video/webm;audio/mpeg;audio/flac;audio/ogg;x-scheme-handler/mpv-uos;
EOF
cp "$ROOT/mpvd/remote/www/icon-512.png" "$PKGDIR/usr/share/icons/hicolor/512x512/apps/$APP_ID.png"
cp "$ROOT/docs/USO.md" "$PKGDIR/usr/share/doc/$PKG/"
cat > "$PKGDIR/usr/share/doc/$PKG/copyright" <<EOF
Upstream-Name: $NAME
Source: $SITE

Files: *
Copyright: $(date +%Y) Soluciones Conscientes
License: GPL-2+
 Este programa es software libre: se puede redistribuir y modificar bajo los términos de la Licencia Pública
 General de GNU, versión 2 o posterior. El texto completo está en /usr/share/common-licenses/GPL-2.
 .
 uosc y thumbfast van dentro con su propia licencia; mpv no va dentro (se usa el del sistema).
EOF
printf '%s (%s) unstable; urgency=low\n\n  * Paquete generado por tools/build_deb.sh.\n\n -- %s  %s\n' \
  "$PKG" "$VERSION" "$MAINTAINER" "$(date -R)" | gzip -9n > "$PKGDIR/usr/share/doc/$PKG/changelog.gz"

# 4b. página de manual: un programa que se instala en /usr/bin la tiene, y además es donde mira quien no quiere
# leerse docs/USO.md. `mpv-uos` comparte la de `atalaya` con un redirigido (.so), que es lo normal en Debian.
mkdir -p "$PKGDIR/usr/share/man/man1"
cat > "$WORK/atalaya.1" <<EOF
.TH ATALAYA 1 "$(LC_ALL=C date +'%B %Y')" "$NAME $VERSION" "Manual de usuario"
.SH NOMBRE
atalaya \- reproductor de vídeo, TV y radio con subtítulos y resúmenes en el propio equipo
.SH SINOPSIS
.B atalaya
.RI [ opciones\ de\ mpv ]
.RI [ archivo\ o\ URL\ ...]
.SH DESCRIPCIÓN
.B $NAME
es mpv con una interfaz pensada para usarse con el mando del sofá. Acepta las mismas opciones y los mismos
archivos y direcciones que mpv, y además televisión y radio por internet, grabaciones programadas, subtítulos
generados y traducidos sin salir del equipo, resúmenes e índice del vídeo, salas para ver a la vez con otra
persona y un mando desde el móvil.
.PP
Usa el
.B mpv
del sistema (probado con 0.41 o posterior) en vez de llevar uno dentro, para no perder la aceleración por
hardware de la máquina; si el del sistema es más viejo, al arrancar dice lo que no va a funcionar.
El trabajo pesado (subtítulos, resúmenes, descargas, índices) lo hace un servicio aparte que nunca compite con
la reproducción: mientras se está viendo algo no arranca nada que no se haya pedido.
.SH ARCHIVOS
.TP
.I ~/.local/share/$APP_ID
Lo tuyo: favoritos, notas, marcas, preferencias y dónde te quedaste.
.TP
.I ~/.cache/$APP_ID
Lo que se puede volver a calcular: subtítulos generados, miniaturas, índices.
.TP
.I /etc/sudoers.d/$APP_ID-rtcwake
Permite que el equipo se despierte solo antes de una grabación programada. Lo pone este paquete y solo autoriza
.IR /usr/lib/$APP_ID/bin/wake ,
que únicamente sabe poner o borrar la alarma del reloj.
.SH ENTORNO
.TP
.B MPV_UOS_MPV
Qué mpv usar, si no el del PATH.
.TP
.B MPV_UOS_DATA_DIR
Dónde guardar lo tuyo.
.TP
.B MPV_UOS_LANG
Idioma de la interfaz: es, en o fr.
.SH VÉASE TAMBIÉN
.BR mpv (1),
y /usr/share/doc/$PKG/USO.md para el manual completo.
.SH SITIO WEB
$SITE
EOF
gzip -9n -c "$WORK/atalaya.1" > "$PKGDIR/usr/share/man/man1/atalaya.1.gz"
printf '.so man1/atalaya.1\n' | gzip -9n > "$PKGDIR/usr/share/man/man1/$APP_ID.1.gz"

# 5. la regla de sudoers del despertador (H40/F4): lo único que este paquete puede hacer y un AppImage no.
# Solo las TRES formas exactas que usa mpvd/power.py, con el valor de -t obligado a ser un número: ninguna de las
# tres escribe ficheros ni ejecuta nada, solo programan o borran la alarma del reloj de la máquina.
cat > "$PKGDIR/etc/sudoers.d/$APP_ID-rtcwake" <<EOF
# Instalado por el paquete de $NAME para que el equipo pueda despertarse solo antes de una grabación programada
# (sin esto la grabación se hace igual, pero solo si el equipo está encendido).
#
# La regla apunta a UN programa, /usr/lib/$APP_ID/bin/wake, que solo sabe hacer tres cosas —poner la alarma del
# reloj para una hora dada en segundos, borrarla y decir si se puede— y que comprueba él mismo que lo que recibe
# son dígitos. No hay comodines, porque sudo ya no los acepta en los argumentos, y por eso tampoco hace falta
# permitir \`rtcwake\` entero: permitirlo dejaría pasar también \`rtcwake -m off\`, que apaga la máquina.
# El fichero es de root (lo instala el paquete), así que nadie puede cambiar lo que se ejecuta con permisos.
#
# Se concede al grupo «sudo», que en Debian y Ubuntu es donde está quien instala el paquete. Para otro usuario o
# grupo, copia la última línea cambiando %sudo por «tu-usuario» o por %tu-grupo.
%sudo ALL=(root) NOPASSWD: /usr/lib/$APP_ID/bin/wake
EOF
chmod 0440 "$PKGDIR/etc/sudoers.d/$APP_ID-rtcwake"
# se valida AQUÍ, al construir: un fichero roto en sudoers.d puede dejar a alguien sin sudo
if command -v visudo >/dev/null; then
  visudo -c -f "$PKGDIR/etc/sudoers.d/$APP_ID-rtcwake" >/dev/null \
    || { echo "la regla de sudoers no es válida: no se empaqueta" >&2; exit 1; }
fi

# 6. control, conffiles y los ganchos
INSTALLED_KB="$(du -sk "$PKGDIR" | cut -f1)"
cat > "$PKGDIR/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_KB
Depends: libc6, mpv, ffmpeg, python3
Recommends: libnotify-bin, util-linux-extra
Section: video
Priority: optional
Homepage: $SITE
Description: Reproductor de vídeo, TV y radio con subtítulos y resúmenes con IA
 $NAME es mpv con una interfaz para usar con el mando del sofá:
 televisión y radio por internet, grabaciones programadas, subtítulos
 generados y traducidos en el propio equipo, resúmenes, índice del vídeo,
 salas para ver a la vez con otra persona y mando desde el móvil.
 .
 Usa el mpv del sistema (probado con 0.41 o posterior) en vez de llevar uno
 dentro, para no perder la aceleración por hardware de la máquina; al
 arrancar avisa de lo que no funcionará si el mpv es más viejo. El trabajo
 pesado lo hace un servicio aparte que nunca compite con la reproducción.
EOF
echo "/etc/sudoers.d/$APP_ID-rtcwake" > "$PKGDIR/DEBIAN/conffiles"
cat > "$PKGDIR/DEBIAN/postinst" <<EOF
#!/bin/sh
set -e
# Si por lo que sea la regla de sudoers no fuera válida, se quita: es mejor quedarse sin despertador que dejar a
# alguien sin poder usar sudo.
if [ -f /etc/sudoers.d/$APP_ID-rtcwake ] && command -v visudo >/dev/null 2>&1; then
  if ! visudo -c -f /etc/sudoers.d/$APP_ID-rtcwake >/dev/null 2>&1; then
    rm -f /etc/sudoers.d/$APP_ID-rtcwake
    echo "$NAME: la regla de sudoers no era válida y se ha quitado; el despertador no funcionará." >&2
  fi
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
exit 0
EOF
cat > "$PKGDIR/DEBIAN/postrm" <<EOF
#!/bin/sh
set -e
if [ "\$1" = "purge" ]; then
  rm -rf /var/lib/$APP_ID
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
exit 0
EOF
chmod 0755 "$PKGDIR/DEBIAN/postinst" "$PKGDIR/DEBIAN/postrm"

# 7. empaquetar. fakeroot para que todo quede de root:root sin pedir permisos de verdad.
# El intérprete del paquete tiene que arrancar: con -B, porque si escribe bytecode deja __pycache__ DENTRO del
# paquete (pasó) y eso son ficheros que no se pueden borrar al desinstalar.
if [ "$ARCH" = "$(dpkg --print-architecture)" ]; then
  "$APP/python/bin/python3.12" -B -c 'import ssl, zlib, sqlite3, ctypes, bz2, lzma, json, asyncio, hashlib' \
    || { echo "el intérprete del paquete no arranca: no se empaqueta" >&2; exit 1; }
fi
find "$APP/python" -name '__pycache__' -type d -prune -exec rm -rf {} +

# Permisos como manda la política: los ficheros del repositorio vienen con los del umask de quien construye
# (0664/0775), y un paquete se instala con 0644/0755. Esto es la diferencia entre 2.700 avisos y ninguno.
find "$PKGDIR" -type d -exec chmod 0755 {} +
find "$PKGDIR" -type f -exec chmod 0644 {} +
chmod 0755 "$PKGDIR/usr/bin/$APP_ID" "$APP/bin/mpv-uos" "$APP/bin/wake" "$PKGDIR/DEBIAN/postinst" \
           "$PKGDIR/DEBIAN/postrm" "$APP/python/bin/python3.12"
find "$APP/python/bin" -type f -exec chmod 0755 {} +
[ -f "$APP/vendor/bin/yt-dlp" ] && chmod 0755 "$APP/vendor/bin/yt-dlp"
for b in "$APP/mpv-config/scripts/uosc/bin/"*linux*; do [ -e "$b" ] && chmod 0755 "$b"; done
chmod 0440 "$PKGDIR/etc/sudoers.d/$APP_ID-rtcwake"
# Despojar los binarios del intérprete (el tarball «stripped» deja algunos módulos con símbolos). Solo se puede
# cuando se construye para la arquitectura de la máquina: `strip` no sabe de otras.
if [ "$ARCH" = "$(dpkg --print-architecture)" ] && command -v strip >/dev/null; then
  # Las BIBLIOTECAS sí, el BINARIO no. Probado una por una: despojar `bin/python3.12` —con --strip-all o con
  # --strip-unneeded— lo deja sin arrancar («undefined symbol: , version», y binutils avisa de que la sección
  # .dynstr se queda fuera del segmento), mientras que despojar libpython y los módulos de lib-dynload no da
  # ningún problema. Lo cazó la comprobación del final de este script, que por eso está aquí.
  find "$APP/python" -type f \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded {} + 2>/dev/null || true
fi
# Los módulos obsoletos de la biblioteca estándar que traen un «#!/usr/local/bin/python» y ya no existen en 3.13
# (cgi, cgitb): nadie los importa aquí y son los únicos ficheros del paquete con un intérprete que no existe.
rm -f "$APP/python/lib/python3.12/cgi.py" "$APP/python/lib/python3.12/cgitb.py"
rm -f "$APP/python/lib/python3.12/ctypes/macholib/fetch_macholib"* \
      "$APP/python/lib/python3.12/encodings/rot_13.py"
# pip no hace falta en caliente (nada de mpvd lo llama; las descargas de modelos son nuestras y verificadas):
# son 12 MB y, de paso, los últimos ficheros con un intérprete que no existe en el equipo
rm -rf "$APP/python/lib/python3.12/site-packages/pip" "$APP/python/lib/python3.12/site-packages/pip-"*
# y los tests que traen las dependencias (jeepney trae los suyos): en un paquete instalado no hacen nada
find "$APP/python/lib/python3.12/site-packages" -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true

mkdir -p "$OUT"
OUTFILE="$OUT/${PKG}_${VERSION}_${ARCH}.deb"
rm -f "$OUTFILE"
fakeroot -- dpkg-deb --root-owner-group -Zxz -b "$PKGDIR" "$OUTFILE" >/dev/null
echo "$OUTFILE ($(du -h "$OUTFILE" | cut -f1))"
