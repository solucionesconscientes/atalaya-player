#!/usr/bin/env bash
# Construye dist/<marca>-<versión>-windows-x86_64.zip (H72, ADR-117): la aplicación, un CPython 3.12 reubicable
# para Windows, yt-dlp.exe y el ayudante de uosc, más los dos lanzadores que ya tiene el proyecto
# (bin\mpv-uos.cmd → bin\mpv-uos.ps1) y tools\install.ps1 para quien quiera accesos directos y los enlaces
# mpv-uos://. Se descomprime donde sea y funciona: no hace falta instalar nada, ni uv, ni un clon del repositorio.
#
# Un .zip y no un instalador: un .exe o un .msi sin firmar se come el aviso de SmartScreen, que asusta más que
# descomprimir una carpeta (decidido con Ser el 2026-10-04). La firma cuesta dinero y hay que renovarla.
#
# mpv NO va dentro, igual que en Linux (ADR-067): se usa el del sistema. El lanzador avisa de cómo instalarlo
# (winget install mpv) si no lo encuentra. En Windows esto es una incomodidad de más que en Linux y está anotado
# en docs/PLATAFORMAS.md, junto con lo más importante: NADA de esto se ha podido ejecutar en un Windows de verdad.
#
# Uso: tools/build_zip_windows.sh [--out DIR]
# Necesita: git, curl, sha256sum, tar, zip (y red la primera vez).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/vendor.lock"
OUT="$ROOT/dist"
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done
for cmd in git curl sha256sum tar zip; do
  command -v "$cmd" >/dev/null || { echo "falta $cmd" >&2; exit 1; }
done

VERSION="$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' \
           "$ROOT/pyproject.toml")"
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/brand.json")"
FILE_NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("folder","mpv-uos"))' \
             "$ROOT/brand.json")"
SITE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("site",""))' "$ROOT/brand.json")"

WORK="$ROOT/tmp/zip-windows"
APP="$WORK/$FILE_NAME"
rm -rf "$WORK"
mkdir -p "$APP"

# 1. la aplicación: lo que lleva git, más los dos lanzadores de Windows y el instalador opcional
git -C "$ROOT" ls-files -z -- bin mpv-config mpvd locales brand.json pyproject.toml vendor.lock README.md \
    docs/USO.md docs/ATAJOS.md docs/PLATAFORMAS.md docs/marca tools/install.ps1 \
    | (cd "$ROOT" && xargs -0 cp --parents -t "$APP")
mkdir -p "$APP/mpv-config/scripts/uosc/bin"
# de los tres ayudantes de uosc, solo el de Windows: los otros dos son 11 MB que aquí no se usan
cp "$ROOT/mpv-config/scripts/uosc/bin/ziggy-windows.exe" "$APP/mpv-config/scripts/uosc/bin/" 2>/dev/null || true
# el ayudante del despertador es de Linux (rtcwake); en Windows lo hace schtasks desde mpvd
rm -f "$APP/bin/wake"
# la puerta de entrada, arriba y con un nombre que no se pueda confundir (ADR-123): un .ps1 no se ejecuta al hacer
# doble clic y el nombre va en ASCII a propósito, porque un acento en el nombre de un fichero dentro de un zip lo
# enseña mal el explorador de Windows si quien lo comprimió no marcó UTF-8
cp "$ROOT/tools/empezar-windows.cmd" "$APP/EMPEZAR-AQUI.cmd"

# 2. el intérprete, en el sitio exacto donde lo busca bin\mpv-uos.ps1: .venv\Scripts\python.exe
tarball="$ROOT/vendor/cpython-windows.tar.gz"
if [ ! -f "$tarball" ]; then
  mkdir -p "$ROOT/vendor"
  curl -fsSL -o "$tarball.part" "$PBS_WINDOWS_URL"
  echo "$PBS_WINDOWS_SHA256  $tarball.part" | sha256sum -c --quiet
  mv "$tarball.part" "$tarball"
fi
mkdir -p "$WORK/py"
tar -xzf "$tarball" -C "$WORK/py"
mkdir -p "$APP/.venv"
mv "$WORK/py/python" "$APP/.venv/Scripts"      # python.exe con su Lib y sus DLL al lado: así se encuentra solo
rm -rf "$APP/.venv/Scripts/Lib/test" "$APP/.venv/Scripts/Lib/idlelib" "$APP/.venv/Scripts/Lib/tkinter" \
       "$APP/.venv/Scripts/Lib/turtledemo" "$APP/.venv/Scripts/Lib/site-packages/pip" \
       "$APP/.venv/Scripts/Lib/site-packages/pip-"* "$APP/.venv/Scripts/tcl" "$APP/.venv/Scripts/DLLs/_tkinter.pyd"
find "$APP/.venv" -name '__pycache__' -type d -prune -exec rm -rf {} +
# los extras ligeros, con ruedas de Windows
mapfile -t pkgs < <(python3 - "$ROOT/pyproject.toml" desktop <<'PYEOF'
import sys, tomllib
data = tomllib.load(open(sys.argv[1], "rb"))
for dep in data["project"]["optional-dependencies"].get(sys.argv[2], []):
    print(dep)
PYEOF
)
if [ "${#pkgs[@]}" -gt 0 ] && command -v uv >/dev/null; then
  uv pip install --quiet --python-platform x86_64-pc-windows-msvc --python-version 3.12 --only-binary :all: \
    --target "$APP/.venv/Scripts/Lib/site-packages" "${pkgs[@]}" || \
    echo "aviso: los extras de escritorio no tienen rueda para Windows; se sigue sin ellos" >&2
fi

# 3. yt-dlp.exe, fijado y comprobado (lo mismo que hace tools/install.ps1 al instalar desde un clon)
mkdir -p "$APP/vendor/bin"
exe="$ROOT/vendor/bin/yt-dlp.exe"
if [ ! -f "$exe" ]; then
  curl -fsSL -o "$exe.part" \
    "https://github.com/yt-dlp/yt-dlp/releases/download/$YTDLP_VERSION/yt-dlp.exe"
  echo "$YTDLP_EXE_SHA256  $exe.part" | sha256sum -c --quiet
  mv "$exe.part" "$exe"
fi
cp "$exe" "$APP/vendor/bin/"
printf '%s\n' "$YTDLP_VERSION" > "$APP/vendor/bin/yt-dlp.exe.version"

# 4. el papel que lee quien lo descomprime
cat > "$APP/LEE-ME.txt" <<EOF
$NAME $VERSION — versión portable para Windows (64 bits)
$SITE

CÓMO SE USA
  1. Descomprime esta carpeta donde quieras (el Escritorio vale).
  2. Doble clic en EMPEZAR-AQUI.cmd, y ya está. Para abrir una película, arrástrala encima.

     Eso busca mpv —el reproductor que $NAME usa por debajo— y, si no lo tienes, lo instala
     con winget («winget install mpv») y sigue. Si winget no estuviera, te dirá en una línea
     qué hacer. No hace falta ser administrador y no se cambia ningún ajuste de tu Windows.

     Windows puede preguntarte «¿Quieres ejecutar este archivo?»: es porque viene de internet
     y no está firmado. Firmar cuesta dinero y caduca, así que lo que puedes hacer en su lugar
     es comprobar la suma SHA-256 del zip, que está publicada en $SITE.

SI QUIERES ACCESOS DIRECTOS Y QUE LOS ENLACES mpv-uos:// FUNCIONEN
  Abre PowerShell en esta carpeta y ejecuta:
      powershell -NoProfile -ExecutionPolicy Bypass -File tools\\install.ps1
  No hace falta ser administrador: todo se queda en tu usuario. Para deshacerlo:
      powershell -NoProfile -ExecutionPolicy Bypass -File tools\\install.ps1 -Uninstall

QUÉ LLEVA DENTRO
  La aplicación, un Python 3.12 propio (no hace falta instalar Python) y yt-dlp.
  NO lleva mpv: se usa el que tengas instalado, para no perder la aceleración por
  hardware de tu tarjeta gráfica. Si el tuyo es anterior al 0.41, al arrancar te
  dirá qué es lo que no va a funcionar.

QUÉ SE GUARDA Y DÓNDE
  Lo tuyo (favoritos, notas, preferencias, dónde te quedaste) en %APPDATA%\\mpv-uos.
  Lo que se puede recalcular (subtítulos generados, miniaturas) en %LOCALAPPDATA%.
  Esta carpeta no se escribe: puedes dejarla en un lápiz USB.

AVISO HONESTO
  Este paquete se construye en Linux y NO se ha podido probar en un Windows de
  verdad. Lo que está comprobado es lo que lleva dentro y que los lanzadores son
  correctos; lo que falta es que alguien lo abra en un Windows y lo cuente.
EOF
unix2dos -q "$APP/LEE-ME.txt" 2>/dev/null || python3 - "$APP/LEE-ME.txt" <<'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
PYEOF

# 5. comprimir
mkdir -p "$OUT"
OUTFILE="$OUT/$FILE_NAME-$VERSION-windows-x86_64.zip"
rm -f "$OUTFILE"
(cd "$WORK" && zip -q -r -9 "$OUTFILE" "$FILE_NAME")
echo "$OUTFILE ($(du -h "$OUTFILE" | cut -f1))"
