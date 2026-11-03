#!/bin/sh
# Avvia (o riavvia) il server della Dispensa in background.
# Le persone si aggiungono dall'app (schermata "Chi sei?"). Qui solo la porta.
PORT=8765

cd "$(dirname "$0")" || exit 1
[ -f server.pid ] && kill "$(cat server.pid)" 2>/dev/null && sleep 1
nohup python3 server.py --port "$PORT" >> server.log 2>&1 &
echo $! > server.pid
echo "Dispensa avviata sulla porta $PORT (log: $(pwd)/server.log)"
