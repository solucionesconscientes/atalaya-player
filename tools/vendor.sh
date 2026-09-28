#!/usr/bin/env bash
# Install or refresh the vendored third-party components (uosc, thumbfast, yt-dlp) pinned in vendor.lock.
# Downloads land in vendor/dl/ (git-ignored) and are verified with SHA-256 before being installed
# into mpv-config/. Safe to re-run; works offline once the downloads are cached.
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source vendor.lock
DL="$ROOT/vendor/dl"
mkdir -p "$DL" "$ROOT/vendor/bin" mpv-config/scripts mpv-config/fonts mpv-config/script-opts

verify() { echo "$1  $2" | sha256sum -c --quiet --status; }

fetch() { # fetch <url> <sha256> <dest> -> 0 ok, 1 unavailable
  local url="$1" sha="$2" dest="$3"
  if [ -f "$dest" ] && verify "$sha" "$dest"; then
    echo "  ok (caché) $(basename "$dest")"; return 0
  fi
  echo "  descargando $(basename "$dest")"
  if ! curl -fsSL --retry 2 -m 300 -o "$dest.part" "$url"; then
    echo "  AVISO: no se pudo descargar $url" >&2; rm -f "$dest.part"; return 1
  fi
  if ! verify "$sha" "$dest.part"; then
    echo "  ERROR: SHA-256 incorrecto para $url" >&2; rm -f "$dest.part"; return 1
  fi
  mv "$dest.part" "$dest"
}

status=0
echo "uosc $UOSC_VERSION"
if fetch "$UOSC_ZIP_URL" "$UOSC_ZIP_SHA256" "$DL/uosc-$UOSC_VERSION.zip"; then
  # The zip ships scripts/uosc/** (Lua + bin/ziggy-*) and fonts/**; extract over mpv-config/.
  unzip -q -o "$DL/uosc-$UOSC_VERSION.zip" -d mpv-config
  echo "  instalado en mpv-config/scripts/uosc (+ fonts, + bin/ziggy)"
else
  status=1
fi
if fetch "$UOSC_CONF_URL" "$UOSC_CONF_SHA256" "$DL/uosc-$UOSC_VERSION.conf"; then
  [ -f mpv-config/script-opts/uosc.conf ] || cp "$DL/uosc-$UOSC_VERSION.conf" mpv-config/script-opts/uosc.conf
fi

echo "thumbfast ${THUMBFAST_COMMIT:0:7}"
if fetch "$THUMBFAST_LUA_URL" "$THUMBFAST_LUA_SHA256" "$DL/thumbfast-$THUMBFAST_COMMIT.lua"; then
  cp "$DL/thumbfast-$THUMBFAST_COMMIT.lua" mpv-config/scripts/thumbfast.lua
else
  status=1
fi
if fetch "$THUMBFAST_CONF_URL" "$THUMBFAST_CONF_SHA256" "$DL/thumbfast-$THUMBFAST_COMMIT.conf"; then
  [ -f mpv-config/script-opts/thumbfast.conf ] || cp "$DL/thumbfast-$THUMBFAST_COMMIT.conf" mpv-config/script-opts/thumbfast.conf
fi

echo "yt-dlp $YTDLP_VERSION"
# vendor/bin/yt-dlp may already hold a NEWER release installed by mpvd's daily updater (verified against the
# upstream SHA2-256SUMS); only (re)install the pinned base when it is missing or MU_VENDOR_FORCE=1.
if [ -x vendor/bin/yt-dlp ] && [ "${MU_VENDOR_FORCE:-0}" != "1" ]; then
  echo "  presente ($(cat vendor/bin/yt-dlp.version 2>/dev/null || echo '?'))"
elif fetch "$YTDLP_URL" "$YTDLP_SHA256" "$DL/yt-dlp-$YTDLP_VERSION"; then
  install -m 0755 "$DL/yt-dlp-$YTDLP_VERSION" vendor/bin/yt-dlp
  echo "$YTDLP_VERSION" > vendor/bin/yt-dlp.version
  echo "  instalado en vendor/bin/yt-dlp"
else
  status=1
fi

# deno (JS runtime for YouTube): only when nothing usable is on PATH, or on demand (MU_VENDOR_DENO=1).
need_deno=1
command -v deno >/dev/null && need_deno=0
if command -v node >/dev/null; then
  major="$(node --version 2>/dev/null | sed -E 's/^v?([0-9]+).*/\1/')"
  [ -n "$major" ] && [ "$major" -ge 22 ] && need_deno=0
fi
if [ -x vendor/bin/deno ]; then
  echo "deno: presente (vendor/bin/deno)"
elif [ "${MU_VENDOR_DENO:-0}" = "1" ] || [ "$need_deno" = "1" ]; then
  arch="$(uname -m)"; os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$arch" in x86_64|amd64) arch=x86_64 ;; aarch64|arm64) arch=aarch64 ;; esac
  case "$os" in linux) triple="$arch-unknown-linux-gnu" ;; darwin) triple="$arch-apple-darwin" ;; *) triple="" ;; esac
  sha_var="DENO_SHA256_${arch}_${os}"
  sha="${!sha_var:-}"
  if [ -n "$triple" ] && [ -n "$sha" ] && command -v unzip >/dev/null; then
    echo "deno $DENO_VERSION ($triple; sin deno/node>=22 en PATH)"
    if fetch "$DENO_BASE_URL/deno-$triple.zip" "$sha" "$DL/deno-$DENO_VERSION-$triple.zip"; then
      unzip -qo "$DL/deno-$DENO_VERSION-$triple.zip" deno -d vendor/bin && chmod 0755 vendor/bin/deno \
        && echo "  instalado en vendor/bin/deno" || status=1
    else
      status=1
    fi
  else
    echo "deno: no vendorizado (sin suma/plataforma para $os/$arch o falta unzip); yt-dlp usará node si existe"
  fi
else
  echo "deno: no necesario (hay deno o node >= 22 en PATH)"
fi

# Sanity: the committed sources must match the pinned version.
if [ -f mpv-config/scripts/uosc/main.lua ]; then
  grep -q "uosc_version = '$UOSC_VERSION'" mpv-config/scripts/uosc/main.lua \
    || { echo "ERROR: mpv-config/scripts/uosc no es la versión $UOSC_VERSION de vendor.lock" >&2; exit 1; }
  [ -x mpv-config/scripts/uosc/bin/ziggy-linux ] || echo "  AVISO: falta ziggy (portapapeles/OpenSubtitles); reejecuta con red." >&2
  [ $status -ne 0 ] && echo "vendor: fuentes presentes, descargas incompletas (sin red)"; exit 0
fi
[ $status -eq 0 ] && echo "vendor OK"
exit $status
