# Dispensa – note per chi lavora sul codice

App per tenere traccia di dispensa, frigo e scadenze, usata in casa da poche persone.
Gira su un NAS UGREEN (Debian, aarch64, utente senza sudo). Il `README.md` è la guida
d'uso e d'installazione per la proprietaria; questo file è per chi modifica il codice.

## Visione (dove sta andando l'app)
La Dispensa deve diventare un **assistente in cucina**. Le tappe, in ordine:
1. **Database dei prodotti in casa** (dove siamo): dispensa, frigo, scadenze, riconoscimento da
   codice a barre e da foto.
2. **Spesa:** lista e piani di spesa costruiti a partire dal database (cosa manca, cosa sta finendo,
   cosa scade) e dalle richieste della persona, con una **chat interna** in cui si chiede
   all'assistente e lui prepara i piani di spesa.
3. **Preparazione:** ricette e guide passo passo che usano quello che c'è in casa.

Quando si progetta qualcosa adesso, tenerne conto: i dati dei prodotti (nome, categoria, quantità,
scadenza, codice) sono la base di spesa e ricette, quindi vanno tenuti puliti e coerenti (vedi la
regola del codice a barre). L'AI passa sempre da `generate()` nel server.

## Stato del lavoro e prossimi passi (aggiornato al 3 novembre 2026, v0.4)
Da qui si riparte: aggiornare questa sezione alla fine di ogni sessione di lavoro.

**Fatto finora** (dettagli in `git log`):
- Aggiornamenti: pulsante "Aggiorna ora", controllo di GitHub ogni 10 minuti e all'apertura,
  finestra "Aggiorna ora / Più tardi", notifica push alla proprietaria, schermata animata.
- Interfaccia in stile iOS: pannelli da tirare giù (maniglia, molla); barra in basso in vetro liquido
  a tutta larghezza con tre schede (Dispensa, Ricette, Menu) e lente trascinabile col dito;
  pulsante rotondo della fotocamera sopra la barra a destra (solo nella scheda Dispensa);
  zoom disattivato; niente rimbalzo della pagina; numero di versione in fondo al Menu.
- Fotocamera: pulsante rotondo = "Riconosci dalla foto" con l'AI (nome, categoria, scadenza,
  codice, quantità); senza AI resta lo scanner. Scanner del codice accanto al campo "Barcode";
  nome dal codice con Open Food Facts (cache `off_cache`); lettura della scadenza dalla foto.
- Regola "un codice a barre = un solo prodotto" (nuova confezione: quantità +1, scadenza nuova).
- Scheda Ricette: prodotti da spuntare + richieste facoltative → ricette dall'AI, ricettario.
  Restano le "Idee per cosa scade" nella pagina Dispensa.
- Lista Dispensa: sezioni per categoria richiudibili (ricordate) e interruttore "Schede / Righe".

**Da verificare sul telefono** (corretti senza poterli riprodurre sul NAS):
- nel Menu la barra in basso scorreva con la pagina (v0.1: niente rimbalzo + overflow-x clip):
  la proprietaria non ha ancora confermato che sia risolto;
- schermo che restava congelato dopo la foto dal pulsante rotondo (v0.x, `whenVisible` + `repaint`):
  confermato che ora la scheda si apre e si compila.

**Idee già discusse, non ancora fatte** (in ordine di priorità proposto):
1. Foto dello **scontrino**: l'AI legge la spesa e propone tutti i prodotti da aggiungere in un colpo
   (deve rispettare la regola del codice a barre e quella della nuova confezione).
2. **Panoramica** in cima alla lista: riquadri "In scadenza / Finiti / Totale / per ambiente" che
   filtrano la lista; "Finiti" è il primo passo verso la lista della spesa.
3. **Ordinamento** (scadenza, nome, categoria, aggiunti di recente) e **indice A–Z** laterale.
4. Poi le tappe della visione: spesa con chat interna, guide passo passo.

**Come lavora la proprietaria:** scrive in italiano e vuole risposte sempre in italiano; preferisce
commit e push diretti su `main` e prova con la notifica / "Aggiorna ora" (niente copia di prova sulla
8799, salvo modifiche rischiose al server, che vanno provate su una copia prima del push).
Le funzioni AI con l'abbonamento Claude funzionano solo per lei. App in uso: porta 8765,
in casa http://192.168.1.202:8765, con Tailscale https://nas-dispensa.tailc2b502.ts.net.

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
- Navigazione: barra in basso con tre schede, `homeView`, `recipesView` (ricette dai prodotti spuntati) e `menuView`;
  si cambia con `showTab("home"|"recipes"|"menu")`.

## Pubblicare una modifica
0. **Numero di versione — obbligatorio a ogni push su `main`.** Aumenta `APP_VERSION` in cima ad
   `app/app.js` (si vede in fondo alla pagina Menu) e, di conseguenza, `CACHE` in `app/sw.js`.
   - piccole modifiche e correzioni: si aumenta il decimale (`0.1` → `0.2` → … → `0.9` → `0.10`);
   - miglioramenti grandi (una funzione nuova importante, una tappa della visione): si aumenta l'unità
     e il decimale torna a 0 (`0.7` → `1.0`).
   - Il messaggio di commit comincia con la versione, es. `v0.2: …`: è il testo che la proprietaria
     vede nella finestra e nella notifica di aggiornamento.
   - Punto di partenza: `0.1` (3 novembre 2026).
1. Prova in locale su un'altra porta, con una **copia** del database:
   `python3 server.py --host 127.0.0.1 --port 8799` (non usare la porta 8765: è l'app in uso).
   Controlli rapidi: `python3 -m py_compile server.py update.py webpush.py` e `node --check app/app.js`.
2. Commit e push su `main`: tutto ciò che è su `main` può essere installato, quindi solo modifiche pronte.
3. Installazione: nell'app Menu › Aggiornamenti › "Aggiorna ora" (solo la proprietaria),
   oppure via SSH `python3 update.py`. Lo script copia il database in `backups/` (ultime 10),
   sostituisce il codice senza toccare i file protetti, scrive `VERSION` e riavvia il server
   (solo il processo della Dispensa, non il NAS). Il server controlla GitHub da solo ogni 10 minuti (e, per la proprietaria, all'apertura dell'app se l'ultimo controllo ha più di 3 minuti): l'app mostra una finestra "Aggiorna ora / Più tardi" una volta per versione, e alla proprietaria arriva una notifica push (una per versione: `notify_update`, testo da `/api/alerts`).
   - Le modifiche a `app/` si vedono anche senza riavvio; quelle ai `.py` solo dopo il riavvio.
   - Un errore in `server.py` installato spegne l'app: va corretto via SSH, quindi prova prima.

## Sul NAS
- `/home/Mattia/dispensa` – copia di lavoro di questo repository (da qui si fa il push).
- `/home/Mattia/Dispensa/dispensa-server` – **app in uso**, con database e credenziali.
  Si aggiorna solo tramite `update.py`: non modificarla a mano e non farci `git`.
- GitHub CLI: `~/.local/bin/gh` (account `metpao99`). Il crontab dell'utente non è disponibile.
