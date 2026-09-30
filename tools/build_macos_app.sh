#!/usr/bin/env bash
# Build dist/MPV-UOS.app (H28, ADR-067): a thin macOS bundle around this checkout — Info.plist (name from brand.json,
# the mpv-uos:// scheme, video/audio document types), an icon and a launcher that runs bin/mpv-uos with the mpv from
# Homebrew or MacPorts. It does not copy the project: move the checkout and rebuild. Built and checked on Linux
# (tests/test_macos_app.py); never run on a Mac yet (docs/PLATAFORMAS.md, NEEDS_HUMAN.md).
# Usage: tools/build_macos_app.sh [--out DIR]
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OUT="$ROOT/dist"
[ "${1:-}" = "--out" ] && OUT="$2"
NAME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/brand.json")"
APP_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("id","mpv-uos"))' "$ROOT/brand.json")"
APP="$OUT/$NAME.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

python3 - "$APP/Contents/Info.plist" "$NAME" "$APP_ID" <<'PYEOF'
import plistlib, sys
path, name, app_id = sys.argv[1:4]
plist = {
    "CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": f"org.mpvuos.{app_id}",
    "CFBundleExecutable": "mpv-uos", "CFBundleIconFile": "mpv-uos.png", "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.1", "CFBundleVersion": "1", "LSMinimumSystemVersion": "11.0",
    "NSHighResolutionCapable": True, "LSApplicationCategoryType": "public.app-category.entertainment",
    "CFBundleURLTypes": [{"CFBundleURLName": f"{name} link", "CFBundleURLSchemes": ["mpv-uos"]}],
    "CFBundleDocumentTypes": [{"CFBundleTypeName": "Vídeo y audio", "CFBundleTypeRole": "Viewer",
                               "LSItemContentTypes": ["public.movie", "public.audio", "public.mpeg-4",
                                                      "org.matroska.mkv", "public.mp3"]}],
}
with open(path, "wb") as fh:
    plistlib.dump(plist, fh)
PYEOF

cat > "$APP/Contents/MacOS/mpv-uos" <<LAUNCH
#!/bin/bash
# Finder starts apps with a minimal PATH: add Homebrew (Apple silicon and Intel) and MacPorts.
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:\$PATH"
ROOT="$ROOT"
if ! command -v mpv >/dev/null 2>&1; then
  osascript -e 'display alert "Falta mpv" message "Instala mpv 0.41 o posterior: brew install mpv"' >/dev/null 2>&1
  exit 1
fi
# Finder passes "-psn_…" on old macOS: drop it
args=(); for a in "\$@"; do case "\$a" in -psn_*) ;; *) args+=("\$a") ;; esac; done
exec "\$ROOT/bin/mpv-uos" "\${args[@]}"
LAUNCH
chmod +x "$APP/Contents/MacOS/mpv-uos"
cp "$ROOT/mpvd/remote/www/icon-512.png" "$APP/Contents/Resources/mpv-uos.png"
printf 'APPL????' > "$APP/Contents/PkgInfo"
echo "$APP"
