#!/usr/bin/env bash
# fetch_cloudflared <linux|windows> <x86_64|aarch64> <destino>
#
# Descarga el asset de cloudflared fijado en vendor.lock para la plataforma pedida y lo verifica con su SHA-256.
# La usan los scripts de empaquetado (incluida la construcción cruzada de arm64) y se sourcea después de cargar
# vendor.lock; no instala nada por su cuenta ni toca el sistema.
fetch_cloudflared() {
  local os="$1" arch="$2" dest="$3" asset var sha
  case "$arch" in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) echo "cloudflared: arquitectura no contemplada: $arch" >&2; return 1 ;;
  esac
  case "$os/$arch" in
    linux/x86_64) asset=cloudflared-linux-amd64 ;;
    linux/aarch64) asset=cloudflared-linux-arm64 ;;
    windows/x86_64) asset=cloudflared-windows-amd64.exe ;;
    *) echo "cloudflared: no hay binario fijado para $os/$arch" >&2; return 1 ;;
  esac
  var="CLOUDFLARED_SHA256_${arch}_${os}"
  sha="${!var:-}"
  if [ -z "$sha" ]; then
    echo "cloudflared: falta $var en vendor.lock" >&2
    return 1
  fi
  if [ -f "$dest" ] && echo "$sha  $dest" | sha256sum -c --quiet --status; then
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  echo "cloudflared $CLOUDFLARED_VERSION ($asset)"
  if ! curl -fsSL --retry 2 -m 300 -o "$dest.part" "$CLOUDFLARED_BASE_URL/$asset"; then
    rm -f "$dest.part"
    echo "cloudflared: no se pudo descargar $asset" >&2
    return 1
  fi
  if ! echo "$sha  $dest.part" | sha256sum -c --quiet; then
    rm -f "$dest.part"
    echo "cloudflared: SHA-256 incorrecto para $asset" >&2
    return 1
  fi
  chmod 0755 "$dest.part"
  mv "$dest.part" "$dest"
}
