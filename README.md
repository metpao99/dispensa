# Dispensa – versione NAS (UGREEN DH2300)

Server Python (solo libreria standard: niente Docker né pip) + app web.
I dati stanno in `dispensa.db` (SQLite) in questa cartella.

## 1. Le persone
Non serve configurare nulla: al primo avvio la schermata "Chi sei?" è vuota.
Scrivi il tuo nome e tocca Aggiungi, poi fai lo stesso per l'altra persona (fino a 10).
Rinominare o rimuovere: Menu › Persone. I prodotti restano anche se una persona viene rimossa.

## Ambienti
L'app parte con due ambienti, Dispensa e Frigo, ognuno con la sua lista (schede in alto).
"+ Ambiente" ne aggiunge altri (Freezer, Cantina…); rinominare o rimuovere: Menu › Ambienti.
La scansione aggiunge il prodotto all'ambiente aperto; per spostarlo, cambia "Ambiente" nella sua scheda.

## Intelligenza artificiale: collegarla dall'app (Menu › Intelligenza artificiale)
Solo la prima persona creata (la proprietaria) vede e cambia queste impostazioni.
**Con il tuo abbonamento Claude (solo per te), tramite Claude Code:**
1. Una sola volta, via SSH: `curl -fsSL https://claude.ai/install.sh | bash`, poi `sh start.sh`.
2. Genera il token con `claude setup-token` (sul NAS via SSH o su un PC): apri il link, accetta, copia il codice `sk-ant-…`.
3. Incollalo nel menu › Collega › Prova collegamento. Vale un anno: un mese prima l'app ti avvisa; "Rinnova token" per sostituirlo.
Le richieste delle altre persone non passano dal tuo abbonamento: per loro resta "Chiedi a Claude/ChatGPT".
**Oppure con una chiave API** (a consumo, vale per tutti): stessa sezione › "Oppure usa una chiave API".
Token e chiave restano solo sul NAS (`claude-token.json`, `ai-key.txt`) e gli aggiornamenti non li toccano.

## Ricette con ciò che scade (facoltativo)
1. Su console.anthropic.com crea un account, carica un piccolo credito e imposta un limite di spesa.
2. Crea una API key (inizia con `sk-ant-`).
3. Sul NAS crea il file `ai-key.txt` in questa cartella con dentro solo la chiave, poi `sh start.sh`.
Compare il pulsante "Idee ricette" quando qualcosa scade entro 7 giorni; le ricette salvate sono in Menu › Ricettario.
Al servizio AI vengono inviati solo i nomi dei prodotti, le quantità e i giorni alla scadenza.
La stessa chiave attiva il pulsante fotocamera accanto a "Scadenza": legge la data dalla foto della confezione (la foto viene inviata al servizio AI).
Senza `ai-key.txt`: il pulsante diventa "Chiedi a Claude" (o ChatGPT, si sceglie in Menu › Ricette per ogni telefono) e apre l'app scelta (con il tuo account) con la domanda già scritta, senza costi aggiuntivi; la lettura della data dalla foto resta nascosta.

## 2. Copia la cartella sul NAS
Dal PC copia tutta la cartella `dispensa-server` in una cartella condivisa del NAS.

## 3. Attiva SSH
UGOS Pro › Pannello di controllo › Terminale › attiva SSH.

## 4. Avvia e prova
Da PC: `ssh TUO_UTENTE@IP-NAS`, poi:
```
python3 --version                       # serve 3.9 o superiore
ls /volume*                             # per trovare il percorso della cartella condivisa
cd /volume1/CARTELLA/dispensa-server    # adatta il percorso
sh start.sh
```
Dall'iPhone sul Wi-Fi di casa apri in Safari `http://IP-NAS:8765`.
Se non si apre: nel firewall di UGOS consenti la porta 8765; errori in `server.log`.

## 5. Avvio automatico all'accensione
`crontab -e` e aggiungi la riga:
```
@reboot sh /volume1/CARTELLA/dispensa-server/start.sh
```
Riavvia il NAS e verifica che l'app risponda.

## 6. Installa sull'iPhone
Safari › `http://IP-NAS:8765` › Condividi › Aggiungi alla schermata Home.
Al primo avvio scegli il tuo nome: il telefono lo ricorda. "Cambia utente" è nel menu.
Menu › Importa backup porta qui i prodotti della versione GitHub.

## Limiti della versione in http (solo rete di casa)
- La fotocamera dal vivo richiede HTTPS: in http usa "Da foto" (stesso risultato, con uno scatto).
- Niente modalità offline completa e accesso solo da casa.
Entrambi si risolvono con Tailscale (HTTPS + accesso da fuori): vedi sotto.

## Fase 2: Tailscale (cartella `tailscale/`)
Prima: crea un account gratuito su tailscale.com e, nella console, DNS › attiva MagicDNS e HTTPS Certificates.
```
cd /volume1/CARTELLA/dispensa-server/tailscale
sh install-tailscale.sh      # scarica Tailscale in questa cartella
sh start-tailscale.sh        # la prima volta: apri il link stampato e autorizza il NAS
```
Poi installa l'app Tailscale sui due iPhone (lei: invitala dalla console › Users).
L'app è su `https://nas-dispensa.<tua-rete>.ts.net` (lo stampa `serve status`):
reinstallala sulla Home da quel nuovo indirizzo (e importa un backup se serve).
Avvio automatico: `crontab -e` e aggiungi
`@reboot sleep 30 && sh /volume1/CARTELLA/dispensa-server/tailscale/start-tailscale.sh`

## Notifiche di scadenza (dopo la fase 2)
Funzionano solo con l'indirizzo https di Tailscale e con l'app aperta dall'icona sulla Home (iOS 16.4+).
Menu › Avvisi di scadenza: scegli quanti giorni prima e a che ora, poi "Attiva su questo telefono" e "Invia una prova".
Ognuno attiva il proprio telefono; il NAS manda un avviso al giorno solo se qualcosa scade.
Dal servizio push di Apple passa solo un segnale vuoto: il testo l'app lo chiede al NAS.
Il file `vapid.json` (creato al primo avvio) è la chiave delle notifiche: non cancellarlo, o vanno riattivate.

## Aggiornare l'app
1. Carica il nuovo zip nella cartella che contiene `dispensa-server` (es. `~/Dispensa`).
2. Via SSH:
```
cd ~/Dispensa/dispensa-server
python3 update.py --zip ../NOME-DELLO-ZIP.zip
```
Lo script fa una copia del database in `backups/` (tiene le ultime 10), sostituisce solo il codice
(mai `dispensa.db`, `vapid.json`, `ai-key.txt`) e riavvia il server. Sui telefoni: chiudi e riapri l'app.
Versione installata: `cat VERSION`.

## Backup
`dispensa.db` sta nella cartella condivisa, quindi rientra nei backup del NAS.
