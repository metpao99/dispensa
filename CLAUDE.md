# Dispensa – note per chi lavora sul codice

App per tenere traccia di dispensa, frigo e scadenze, usata in casa da poche persone.
Gira su un NAS UGREEN (Debian, aarch64, utente senza sudo). Il `README.md` è la guida
d'uso e d'installazione per la proprietaria; questo file è per chi modifica il codice.

## Struttura
- `server.py` – server HTTP (ThreadingHTTPServer) + API JSON + file statici di `app/`.
  L'elenco delle API è nella docstring in cima al file: tienilo aggiornato.
- `app/` – web app (PWA) che gira **sul telefono** in Safari: `index.html` (HTML + CSS),
  `app.js` (tutta la logica, JavaScript senza framework), `sw.js` (service worker, cache offline).
- `webpush.py` – notifiche Web Push (VAPID) scritte a mano.
- `update.py` – aggiornamento da GitHub o da zip; usato anche dal pulsante "Aggiorna ora".
- `start.sh` – avvia/riavvia il server (porta 8765, `server.pid`, `server.log`).
- `tailscale/` – script per HTTPS e accesso da fuori (nel repo solo i `.sh`).
- `tools/build_demo.py` – genera la versione di prova senza NAS: simula le API in JavaScript.

## Regole
- **Solo libreria standard Python (3.9+)**: niente pip, niente Docker, niente dipendenze.
  Nel frontend niente build step né npm: i file si servono così come sono.
- **Testi dell'interfaccia e commenti in italiano.**
- **Mai nel repository dati o credenziali** (il repo è pubblico): `dispensa.db*`, `backups/`,
  `vapid.json`, `claude-token.json`, `ai-key.txt`, `github-token.txt`, log e pid. Sono in `.gitignore`
  e nell'insieme `PROTECTED` di `update.py`: un nuovo file di dati va aggiunto in **entrambi**.
- Quando cambi i file in `app/`, **aumenta il numero in `CACHE` di `app/sw.js`** (`dispensa-nas-vN`).
- Una nuova API in `server.py` va simulata anche in `tools/build_demo.py`, altrimenti la prova si rompe.
- Le persone non hanno password: la **proprietaria** è la prima persona creata (`owner_id()`).
  Impostazioni AI e aggiornamenti sono riservati a lei, sia nel server (403) sia nell'interfaccia.
- Gli accessi al database passano da `db()` sotto `_lock`.
- **Il codice a barre identifica il prodotto, in tutti gli ambienti: un codice = una sola voce.**
  Se un codice (scansionato, letto dalla foto o scritto a mano) esiste già, non si crea un doppione:
  si riapre quella voce come "nuova confezione" (quantità +1, scadenza della nuova confezione),
  tenendo nome, categoria, note e ambiente. Funzioni: `byCode()`, `openRestock()`, controllo in `saveForm()`.
  Qualsiasi nuovo modo di aggiungere prodotti deve passare da questa regola.
- Pannelli (`.sheet`): aprili e chiudili con `openSheet(id)` / `closeSheet(id)`, non con `.hidden`,
  così hanno l'animazione e il gesto "tira giù per chiudere". Se la chiusura deve fare altro, registrala
  in `sheetClosers[id]` (es. `closeForm` per la scheda prodotto).
- Navigazione: barra in basso con due schede, `homeView` e `menuView`; si cambia con `showTab("home"|"menu")`.

## Pubblicare una modifica
1. Prova in locale su un'altra porta, con una **copia** del database:
   `python3 server.py --host 127.0.0.1 --port 8799` (non usare la porta 8765: è l'app in uso).
   Controlli rapidi: `python3 -m py_compile server.py update.py webpush.py` e `node --check app/app.js`.
2. Commit e push su `main`: tutto ciò che è su `main` può essere installato, quindi solo modifiche pronte.
3. Installazione: nell'app Menu › Aggiornamenti › "Aggiorna ora" (solo la proprietaria),
   oppure via SSH `python3 update.py`. Lo script copia il database in `backups/` (ultime 10),
   sostituisce il codice senza toccare i file protetti, scrive `VERSION` e riavvia il server
   (solo il processo della Dispensa, non il NAS). Il server controlla GitHub da solo ogni 10 minuti (e, per la proprietaria, all'apertura dell'app se l'ultimo controllo ha più di 3 minuti): l'app mostra una finestra "Aggiorna ora / Più tardi" una volta per versione.
   - Le modifiche a `app/` si vedono anche senza riavvio; quelle ai `.py` solo dopo il riavvio.
   - Un errore in `server.py` installato spegne l'app: va corretto via SSH, quindi prova prima.

## Sul NAS
- `/home/Mattia/dispensa` – copia di lavoro di questo repository (da qui si fa il push).
- `/home/Mattia/Dispensa/dispensa-server` – **app in uso**, con database e credenziali.
  Si aggiorna solo tramite `update.py`: non modificarla a mano e non farci `git`.
- GitHub CLI: `~/.local/bin/gh` (account `metpao99`). Il crontab dell'utente non è disponibile.
