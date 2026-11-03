#!/bin/sh
# Avvia Tailscale in modalità userspace (non tocca la rete del NAS)
# e pubblica la Dispensa in HTTPS sulla tua rete Tailscale.
cd "$(dirname "$0")" || exit 1
TS="./tailscale --socket=./state/tailscaled.sock"

[ -f tailscaled.pid ] && kill "$(cat tailscaled.pid)" 2>/dev/null && sleep 1
nohup ./tailscaled --tun=userspace-networking --statedir=./state \
  --socket=./state/tailscaled.sock >> tailscaled.log 2>&1 &
echo $! > tailscaled.pid
sleep 4

# La prima volta stampa un link da aprire per autorizzare il NAS; le volte successive si ricollega da solo.
$TS up --hostname=nas-dispensa
# Pubblica http://localhost:8765 come https://nas-dispensa.<tua-rete>.ts.net (resta salvato).
$TS serve --bg 8765
$TS serve status
