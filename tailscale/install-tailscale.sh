#!/bin/sh
# Scarica la versione ufficiale di Tailscale per ARM64 in QUESTA cartella.
# Non installa nulla nel sistema del NAS: per rimuoverla basta cancellare la cartella.
set -e
cd "$(dirname "$0")"

ARCH=$(uname -m)
case "$ARCH" in
  aarch64|arm64) TS_ARCH=arm64 ;;
  x86_64)        TS_ARCH=amd64 ;;
  *) echo "Architettura non prevista: $ARCH"; exit 1 ;;
esac

get() { if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"; else wget -qO- "$1"; fi; }

echo "Cerco l'ultima versione stabile..."
FILE=$(get "https://pkgs.tailscale.com/stable/?mode=json" | python3 -c "import json,sys; print(json.load(sys.stdin)['Tarballs']['$TS_ARCH'])")
echo "Scarico $FILE"
get "https://pkgs.tailscale.com/stable/$FILE" > ts.tgz
tar xzf ts.tgz
DIR=$(basename "$FILE" .tgz)
cp "$DIR/tailscale" "$DIR/tailscaled" .
rm -rf "$DIR" ts.tgz
chmod +x tailscale tailscaled
mkdir -p state
./tailscale version
echo "Fatto. Ora: sh start-tailscale.sh"
