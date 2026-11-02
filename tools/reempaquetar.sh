#!/usr/bin/env bash
# Reempaquetar Atalaya (AppImage x86_64, .deb amd64, zip de Windows, instalador NSIS, latest.json y SHA256SUMS) y
# comprobar POR DENTRO que cada paquete lleva el código nuevo: se busca una cadena del panel de canales (ADR-138)
# dentro de lo extraído. Un paquete que no la lleve es un fallo, no un aviso.
#
# Antes de compilar, los ejecutables que hay en dist/ se apartan a dist/antiguos/<AAAA-MM-DD>/ con su SHA256SUMS:
# un paquete nuevo no borra el anterior (si la build sale mal, la de ayer tiene que seguir existiendo).
#
# Uso: tools/reempaquetar.sh
# Necesita: lo mismo que cada build_*.sh (uv, dpkg-deb, makensis, zip, 7z, unzip) y red la primera vez.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
ROOT="$PWD"
OUT="$ROOT/dist"
LOG="$ROOT/tmp/reempaquetar.log"
MARCA="follow_selection"   # solo existe en el panel de canales de ADR-138 (mu-iptv y el parche de uosc)
mkdir -p "$OUT" "$ROOT/tmp"
: > "$LOG"
fail=0

# Los paquetes de la tanda anterior se guardan enteros, con sus sumas, antes de que los sobrescriba el build.
archivar() {
    local viejo="$OUT/antiguos/$(date +%F)" n=0 f
    for f in "$OUT"/*.AppImage "$OUT"/*.deb "$OUT"/*.zip "$OUT"/*.exe; do
        [ -f "$f" ] || continue
        mkdir -p "$viejo"
        mv -f "$f" "$viejo/"
        n=$((n + 1))
    done
    for f in latest.json SHA256SUMS; do
        [ -f "$OUT/$f" ] && mv -f "$OUT/$f" "$viejo/"
    done
    if [ "$n" -gt 0 ]; then
        ( cd "$viejo" && sha256sum *.AppImage *.deb *.zip *.exe 2>/dev/null > SHA256SUMS )
        echo "    apartados $n paquete(s) a dist/antiguos/$(date +%F) (con su SHA256SUMS)" | tee -a "$LOG"
    fi
}

paso() {  # $1 = etiqueta, resto = comando
    local n="$1"; shift
    echo "=== $n — $(date +%H:%M:%S) ===" | tee -a "$LOG"
    if nice -n 19 "$@" >>"$LOG" 2>&1; then
        echo "    OK $n" | tee -a "$LOG"
    else
        local rc=$?
        echo "    FALLO $n (rc=$rc); ver $LOG" | tee -a "$LOG"
        fail=1
    fi
}

verificar() {  # $1 = etiqueta, $2 = fichero
    local etiqueta="$1" fichero="$2" tmp hits
    if [ ! -f "$fichero" ]; then
        echo "    FALLO $etiqueta: no existe $fichero" | tee -a "$LOG"
        fail=1
        return
    fi
    tmp="$(mktemp -d "$ROOT/tmp/verificar-XXXXXX")" || { fail=1; return; }
    case "$fichero" in
        *.AppImage) ( cd "$tmp" && "$fichero" --appimage-extract >/dev/null 2>&1 ) ;;
        *.deb) dpkg-deb -x "$fichero" "$tmp" >/dev/null 2>&1 ;;
        *.zip) unzip -q "$fichero" -d "$tmp" >/dev/null 2>&1 ;;
        *.exe) 7z x -y -o"$tmp" "$fichero" >/dev/null 2>&1 ;;
    esac
    hits="$(grep -R -a -l --include='*.lua' -F "$MARCA" "$tmp" 2>/dev/null | head -3)"
    if [ -n "$hits" ]; then
        echo "    OK $etiqueta lleva «$MARCA»: $(echo "$hits" | sed "s|$tmp/||" | tr '\n' ' ')" | tee -a "$LOG"
    else
        echo "    FALLO $etiqueta NO lleva «$MARCA» (paquete viejo o sin el código nuevo)" | tee -a "$LOG"
        fail=1
    fi
    rm -rf "$tmp"
}

echo "git: $(git log --oneline -1)" | tee -a "$LOG"
echo "sin commit: $(git status --porcelain | wc -l) fichero(s) modificados en el árbol" | tee -a "$LOG"

echo "=== apartar la build anterior ===" | tee -a "$LOG"
archivar

paso "AppImage x86_64" tools/build_appimage.sh --arch x86_64
paso "deb amd64" tools/build_deb.sh --arch amd64
paso "zip Windows" tools/build_zip_windows.sh
paso "instalador NSIS" tools/build_win_installer.sh

echo "=== latest.json ===" | tee -a "$LOG"
if python3 tools/gen_latest_json.py >>"$LOG" 2>&1; then
    echo "    OK latest.json" | tee -a "$LOG"
else
    echo "    FALLO latest.json" | tee -a "$LOG"; fail=1
fi

echo "=== sumas ===" | tee -a "$LOG"
( cd "$OUT" && sha256sum *.AppImage *.deb *.zip *.exe 2>/dev/null > SHA256SUMS && wc -l SHA256SUMS ) | tee -a "$LOG"

echo "=== comprobar el código nuevo dentro de cada paquete ===" | tee -a "$LOG"
verificar "AppImage x86_64" "$(ls -t "$OUT"/*x86_64.AppImage 2>/dev/null | head -1)"
verificar "deb amd64" "$(ls -t "$OUT"/*_amd64.deb 2>/dev/null | head -1)"
verificar "zip Windows" "$(ls -t "$OUT"/*windows-x86_64.zip 2>/dev/null | head -1)"
verificar "instalador NSIS" "$(ls -t "$OUT"/*-setup.exe 2>/dev/null | head -1)"

echo "=== dist resultante ===" | tee -a "$LOG"
ls -l --time-style=+%d-%m\ %H:%M "$OUT" | awk '{print $5, $6, $7, $8}' | tee -a "$LOG"
echo "=== fin $(date +%H:%M:%S) ===" | tee -a "$LOG"
if [ "$fail" -ne 0 ]; then echo "❌ reempaquetar: algo falló (ver $LOG)"; exit 1; fi
echo "✅ reempaquetar OK"
