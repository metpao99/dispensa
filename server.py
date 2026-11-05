#!/usr/bin/env python3
"""
Dispensa – server minimo per NAS (solo libreria standard Python 3.9+).

Avvio:   python3 server.py --port 8765
         (le persone si aggiungono dall'app; --users "Aurora" crea nomi iniziali)
Dati:    dispensa.db (SQLite) nella stessa cartella.

API
  GET    /api/users          elenco nomi (per la schermata "Chi sei?")
  POST   /api/users          {"name": "..."} aggiunge una persona
  PATCH  /api/users          {"from": "...", "to": "..."} rinomina
  DELETE /api/users/<nome>   rimuove una persona (i prodotti restano)
  POST   /api/login          {"name": "..."} -> imposta il cookie del telefono
  POST   /api/logout         dimentica questo telefono
  GET    /api/me             utente riconosciuto dal cookie, 401 se nessuno
  GET    /api/items          tutti i prodotti + ambienti
  POST   /api/spaces         {"name": "..."} aggiunge un ambiente (Dispensa, Frigo…)
  PATCH  /api/spaces         {"id": "...", "name": "..."} rinomina
  DELETE /api/spaces/<id>    rimuove un ambiente vuoto
  POST   /api/recipes        idee di ricette con ciò che scade; con {"scelti": [id…], "richiesta": "..."}
                             ricette con i prodotti spuntati nella scheda Ricette (serve l'AI)
  GET    /api/saved-recipes  ricettario; PUT/DELETE /api/saved-recipes/<id>
  GET    /api/push/key | /api/push/settings, POST /api/push/settings | subscribe | unsubscribe | test
  GET    /api/alerts         testo dell'avviso per chi chiede (usato dal service worker)
  POST   /api/read-date      {"image": base64 JPEG} -> legge la scadenza dalla foto (serve ai-key.txt)
  POST   /api/recognize      {"image": base64 JPEG} -> riconosce il prodotto dalla foto: nome, categoria, pezzi, scadenza (AI)
  GET    /api/product/<code> nome, marca, formato e categoria da Open Food Facts (con cache nel database)
  PUT    /api/items/<id>     crea/aggiorna un prodotto
  DELETE /api/items/<id>     elimina un prodotto
  POST   /api/items/<id>/to-shopping  sposta UNA confezione nella lista della spesa (atomico): aggiunge in lista
                             (stesso codice o stesso nome = quantità +1) e poi toglie 1 dalla dispensa
                             (quantità 1 o 0: il prodotto viene eliminato). -> {"entry", "created", "item"|null}
  GET    /api/shopping       lista della spesa condivisa: {"items": [...], "stores": [negozi preferiti],
                             "where": {negozio: {id voce: stima}}} (stime già in cache)
  POST   /api/shop-stores    {"stores": ["Esselunga", "Lidl", …]} negozi preferiti della casa (massimo 8)
  POST   /api/shopping/where {"store": "...", "fresh": false} stima AI (generate) di cosa si trova di solito in quel
                             negozio, per le voci non ancora stimate (con "fresh" rifà tutte); in cache per 90 giorni.
                             È una stima, non una verifica dello scaffale. -> {"where": {id voce: stima}}
  POST   /api/shopping       {"name", "cat", "code", "qty", "space"} aggiunge una voce (stesso codice = quantità +qty)
  PATCH  /api/shopping/<id>  {"qty"?, "done"?, "name"?, "cat"?} cambia quantità, "comprato", nome, categoria
  DELETE /api/shopping/<id>  toglie una voce; DELETE /api/shopping toglie tutte quelle spuntate (comprate)
  GET    /api/update         versione installata e ultima su GitHub (controllo ogni 10 minuti; per la proprietaria anche all'apertura)
  POST   /api/update/check   controlla subito GitHub (solo la proprietaria)
  POST   /api/update/run     avvia update.py: copia del database, aggiornamento, riavvio (solo la proprietaria)
Tutto il resto viene servito dalla cartella ./app
"""
import webpush
import update as updater   # lo stesso script che si usa via SSH (python3 update.py)
import shutil, subprocess, tempfile
import argparse, sys, datetime, json, urllib.error, urllib.request, mimetypes, os, re, secrets, sqlite3, threading, time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE, "app")
DB_PATH = os.path.join(BASE, "dispensa.db")
COOKIE = "dispensa_session"
ONE_YEAR = 365 * 24 * 3600
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
FIELDS = ("code", "name", "space", "qty", "exp", "cat", "note")
MAX_SPACES = 12
AI_KEY_FILE = os.path.join(BASE, "ai-key.txt")
AI_MODEL = os.environ.get("DISPENSA_AI_MODEL", "claude-haiku-5-5")


CLAUDE_TOKEN_FILE = os.path.join(BASE, "claude-token.json")


class AIError(Exception):
    pass


def claude_bin():
    """Claude Code installato con l'installer ufficiale (~/.local/bin) o nel PATH."""
    p = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def claude_token():
    try:
        with open(CLAUDE_TOKEN_FILE) as f:
            d = json.load(f)
        return d if d.get("token") else None
    except (OSError, ValueError):
        return None


def save_secret(path, text):
    with open(path, "w") as f:
        f.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def owner_id():
    r = db().execute("SELECT MIN(id) FROM users").fetchone()
    return r[0] if r else None


def engine_for(user):
    """Chiave API: per tutti. Claude Code con l'abbonamento: solo per la proprietaria (prima persona creata)."""
    if ai_key():
        return "api"
    if user and user["id"] == owner_id() and claude_token() and claude_bin():
        return "claude"
    return None


def run_claude(prompt, image_b64=None):
    tok = claude_token()
    if not tok or not claude_bin():
        raise AIError("Claude Code non collegato: vai in Menu › Intelligenza artificiale")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok["token"]
    with tempfile.TemporaryDirectory(prefix="dispensa-ai-") as tmp:
        if image_b64:
            import base64
            with open(os.path.join(tmp, "foto.jpg"), "wb") as f:
                f.write(base64.b64decode(image_b64))
            prompt = "Apri e guarda l'immagine ./foto.jpg nella cartella corrente.\n\n" + prompt
        try:
            r = subprocess.run([claude_bin(), "-p", prompt, "--output-format", "text", "--allowedTools", "Read"],
                               cwd=tmp, env=env, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise AIError("Claude Code non ha risposto in tempo: riprova")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").lower()
        if any(w in err for w in ("expired", "401", "invalid", "unauthorized", "not logged", "oauth")):
            raise AIError("Collegamento a Claude scaduto o non valido: rifallo da Menu › Intelligenza artificiale")
        if "limit" in err:
            raise AIError("Limite d'uso dell'abbonamento raggiunto: riprova più tardi")
        raise AIError("Claude Code ha dato errore: " + (r.stderr or r.stdout or "sconosciuto").strip()[:160])
    return r.stdout


def generate(user, prompt, image_b64=None):
    eng = engine_for(user)
    if eng == "api":
        try:
            return ask_ai(prompt, image_b64)
        except urllib.error.HTTPError as e:
            msg = {401: "Chiave API non valida", 429: "Troppe richieste: riprova tra poco"}.get(e.code, f"Servizio AI: errore {e.code}")
            if e.code == 400 and b"credit" in e.read().lower():
                msg = "Credito API esaurito"
            raise AIError(msg)
        except (urllib.error.URLError, TimeoutError):
            raise AIError("Servizio AI non raggiungibile: il NAS è online?")
    if eng == "claude":
        return run_claude(prompt, image_b64)
    raise AIError("Intelligenza artificiale non attiva: vai in Menu › Intelligenza artificiale")


def ai_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"].strip()
    try:
        with open(AI_KEY_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def days_left(exp):
    try:
        return (datetime.date.fromisoformat(exp) - datetime.date.today()).days
    except (TypeError, ValueError):
        return None


def recipe_prompt(items):
    """items: lista di dict con name, qty, exp. Stesso testo della versione di prova (app.js)."""
    soon, other = [], []
    for it in items:
        if int(it.get("qty") or 0) <= 0:
            continue
        d = days_left(it.get("exp"))
        if d is not None and d < 0:
            continue                      # scaduti: mai proposti
        if d is not None and d <= 7:
            soon.append((d, it))
        else:
            other.append(it)
    soon.sort(key=lambda x: x[0])
    lines_soon = "\n".join(f"- {it['name']} · scade tra {d} giorni · quantità {it.get('qty')}" for d, it in soon)
    lines_other = "\n".join(f"- {it['name']}" for it in other[:60]) or "- (nient'altro)"
    return f"""Sei un aiuto in cucina per una casa italiana. Proponi 3 ricette casalinghe per 2 persone che consumino prima i prodotti in scadenza.

Prodotti in scadenza (nome come lo scriviamo noi · giorni alla scadenza · quantità):
{lines_soon}

Altri prodotti disponibili in casa:
{lines_other}

Regole: ogni ricetta usa almeno un prodotto in scadenza, preferendo quelli più vicini alla scadenza; usa anche gli altri prodotti di casa quando ha senso; dai per scontati sale, pepe, olio, aceto e acqua; elenca solo gli ingredienti che mancano davvero. Passi brevi e pratici.

Rispondi SOLO con JSON valido, senza testo prima o dopo, in questa forma:
{{"ricette":[{{"titolo":"...","tempo":"25 min","perche":"una frase su cosa salva dalla scadenza","usa":["nomi esatti dei prodotti di casa usati"],"mancano":["ingredienti da comprare"],"passi":["...","..."]}}]}}""", len(soon)


def pick_prompt(chosen, others, wish=""):
    """Ricette con i prodotti scelti dalla persona (scheda Ricette). chosen/others: liste di dict."""
    def line(it):
        d = days_left(it.get("exp"))
        extra = f" · scade tra {d} giorni" if d is not None and 0 <= d <= 7 else ""
        return f"- {it['name']}{extra}"
    lines_chosen = "\n".join(line(it) for it in chosen)
    lines_other = "\n".join(f"- {it['name']}" for it in others[:60]) or "- (nient'altro)"
    wish = re.sub(r"\s+", " ", wish or "").strip()[:300]
    return f"""Sei un aiuto in cucina per una casa italiana. Proponi 3 ricette casalinghe per 2 persone costruite intorno ai prodotti che ho scelto.

Prodotti scelti (da usare):
{lines_chosen}

Altri prodotti disponibili in casa (usali solo se servono):
{lines_other}
{"" if not wish else chr(10) + "Le mie richieste: " + wish + chr(10)}
Regole: ogni ricetta usa più prodotti scelti possibile (almeno uno); se un prodotto scelto è vicino alla scadenza, dagli la precedenza; dai per scontati sale, pepe, olio, aceto e acqua; elenca solo gli ingredienti che mancano davvero. Passi brevi e pratici.

Rispondi SOLO con JSON valido, senza testo prima o dopo, in questa forma:
{{"ricette":[{{"titolo":"...","tempo":"25 min","perche":"una frase su come usa i prodotti scelti","usa":["nomi esatti dei prodotti di casa usati"],"mancano":["ingredienti da comprare"],"passi":["...","..."]}}]}}"""


def parse_recipes(text):
    a, b = text.find("{"), text.rfind("}")
    data = json.loads(text[a:b + 1])
    out = []
    for r in data.get("ricette", [])[:5]:
        if not isinstance(r, dict) or not r.get("titolo"):
            continue
        out.append({
            "titolo": str(r.get("titolo"))[:120], "tempo": str(r.get("tempo") or "")[:30],
            "perche": str(r.get("perche") or "")[:300],
            "usa": [str(x)[:120] for x in (r.get("usa") or [])][:12],
            "mancano": [str(x)[:120] for x in (r.get("mancano") or [])][:12],
            "passi": [str(x)[:500] for x in (r.get("passi") or [])][:15]})
    return out


VAPID_FILE = os.path.join(BASE, "vapid.json")
VAPID = None   # (privata, pubblica) caricate all'avvio


def alerts_for(user_id):
    row = db().execute("SELECT days FROM push_prefs WHERE user_id = ?", (user_id,)).fetchone()
    days = row["days"] if row else 3
    names = {r["id"]: r["name"] for r in db().execute("SELECT id, name FROM spaces")}
    soon = []
    for r in db().execute("SELECT data FROM items"):
        it = json.loads(r["data"])
        d = days_left(it.get("exp"))
        if d is not None and 0 <= d <= days and int(it.get("qty") or 0) > 0:
            soon.append((d, it["name"], names.get(it.get("space"), "")))
    soon.sort()
    when = lambda d: "oggi" if d == 0 else "domani" if d == 1 else f"tra {d} giorni"
    parts = [f"{n} ({sp}) {when(d)}" if sp else f"{n} {when(d)}" for d, n, sp in soon[:6]]
    if len(soon) > 6:
        parts.append(f"e altri {len(soon) - 6}")
    title = "1 prodotto in scadenza" if len(soon) == 1 else f"{len(soon)} prodotti in scadenza"
    return {"count": len(soon), "title": title, "body": " · ".join(parts)}


def push_user(user_id):
    """Invia un segnale a tutti i telefoni della persona; toglie le iscrizioni scadute."""
    subs = db().execute("SELECT endpoint, origin FROM push_subs WHERE user_id = ?", (user_id,)).fetchall()
    sent, errors = 0, []
    for sub in subs:
        try:
            st = webpush.send(VAPID[0], VAPID[1], sub["endpoint"], sub["origin"] or "mailto:dispensa@localhost")
        except Exception as e:          # rete assente, DNS, ecc.
            errors.append(str(e)[:80]); continue
        if st in (200, 201, 202):
            sent += 1
        elif st in (404, 410):
            db().execute("DELETE FROM push_subs WHERE endpoint = ?", (sub["endpoint"],))
        else:
            errors.append(f"HTTP {st}")
    db().commit()
    return sent, errors


def push_loop():
    """Ogni 10 minuti: chi ha passato l'ora scelta e non ha ancora ricevuto l'avviso di oggi lo riceve."""
    while True:
        try:
            now = datetime.datetime.now()
            today = now.date().isoformat()
            with _lock:
                rows = db().execute("SELECT p.user_id, p.hour, p.last_sent FROM push_prefs p "
                                    "WHERE EXISTS (SELECT 1 FROM push_subs s WHERE s.user_id = p.user_id)").fetchall()
                for r in rows:
                    if now.hour >= r["hour"] and r["last_sent"] != today:
                        if alerts_for(r["user_id"])["count"]:
                            push_user(r["user_id"])
                        db().execute("UPDATE push_prefs SET last_sent = ? WHERE user_id = ?", (today, r["user_id"]))
                db().commit()
        except Exception as e:
            print("push_loop:", e, flush=True)
        time.sleep(600)


def clean_prefs(body):
    days = max(0, min(30, int(body.get("days", 3))))
    hour = max(0, min(23, int(body.get("hour", 9))))
    return days, hour


DATE_PROMPT = """Nella foto c'è la data di scadenza di un prodotto alimentare (vicino a scritte come "da consumarsi entro", "da consumarsi preferibilmente entro", "TMC", "scad.", "exp", "best before"). Oggi è {today}.
Regole: formato europeo giorno/mese/anno; anno a due cifre = 20xx; se ci sono solo mese e anno usa l'ultimo giorno di quel mese; se ci sono più date (produzione, lotto, scadenza) scegli quella di scadenza.
Rispondi SOLO con JSON valido: {{"data":"AAAA-MM-GG oppure null","letto":"il testo della data così come è stampato"}}"""


RECOGNIZE_PROMPT = """Nella foto c'è un prodotto da dispensa, frigo o casa (confezione, frutta, verdura, prodotto del banco…). Oggi è {today}.
Riconoscilo e rispondi SOLO con JSON valido:
{{"nome":"nome breve con marca e formato se si leggono, es. «Pelati Mutti 400 g» o «Mele Golden»",
"categoria":"una categoria breve in italiano",
"pezzi":numero di confezioni o pezzi uguali visibili (1 se è uno solo),
"scadenza":"AAAA-MM-GG se una data di scadenza è leggibile, altrimenti null",
"codice":"le cifre del codice a barre se sono leggibili per intero, altrimenti null"}}
Per la categoria usa, se adatta, una di queste che sono già in uso: {cats}.
Per la scadenza: formato europeo giorno/mese/anno, anno a due cifre = 20xx, solo mese e anno = ultimo giorno del mese.
Se nella foto non c'è un prodotto riconoscibile, metti "nome": null."""


def parse_recognize_answer(text):
    a, b = text.find("{"), text.rfind("}")
    data = json.loads(text[a:b + 1])
    exp = data.get("scadenza")
    try:
        exp = datetime.date.fromisoformat(str(exp)).isoformat() if exp else None
    except ValueError:
        exp = None
    try:
        n = max(1, min(99, int(data.get("pezzi") or 1)))
    except (TypeError, ValueError):
        n = 1
    code = re.sub(r"\D", "", str(data.get("codice") or ""))
    return {"nome": str(data.get("nome") or "").strip()[:200] or None,
            "categoria": str(data.get("categoria") or "").strip()[:60],
            "pezzi": n, "scadenza": exp, "codice": code if 8 <= len(code) <= 14 else ""}


# ---------- Open Food Facts: nome del prodotto dal codice a barre ----------
OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json?fields=product_name,product_name_it,brands,quantity,categories_tags"
OFF_UA = "Dispensa-NAS/1.0 (app domestica)"
OFF_TTL = {True: 30 * 86400, False: 86400}      # trovato: un mese; non trovato: riprova dopo un giorno
# dal tag più specifico al più generico: vince il primo che compare tra le categorie del prodotto
OFF_CATS = [
    ("en:frozen-foods", "Surgelati"), ("en:pastas", "Pasta"), ("en:rices", "Riso"), ("en:legumes", "Legumi"),
    ("en:canned-foods", "Conserve"), ("en:tomato-sauces", "Sughi"), ("en:sauces", "Sughi"), ("en:olive-oils", "Olio"),
    ("en:vegetable-oils", "Olio"), ("en:vinegars", "Condimenti"), ("en:condiments", "Condimenti"), ("en:spices", "Spezie"),
    ("en:salts", "Condimenti"), ("en:cheeses", "Formaggi"), ("en:yogurts", "Latticini"), ("en:milks", "Latticini"),
    ("en:dairies", "Latticini"), ("en:eggs", "Uova"), ("en:hams", "Salumi"), ("en:prepared-meats", "Salumi"),
    ("en:meats", "Carne"), ("en:fishes", "Pesce"), ("en:seafood", "Pesce"), ("en:breads", "Pane"),
    ("en:biscuits", "Biscotti"), ("en:breakfast-cereals", "Colazione"), ("en:chocolates", "Dolci"),
    ("en:confectioneries", "Dolci"), ("en:sweet-spreads", "Colazione"), ("en:honeys", "Colazione"),
    ("en:jams", "Colazione"), ("en:coffees", "Caffè e tè"), ("en:teas", "Caffè e tè"), ("en:waters", "Bevande"),
    ("en:wines", "Vino e alcolici"), ("en:beers", "Vino e alcolici"), ("en:alcoholic-beverages", "Vino e alcolici"),
    ("en:beverages", "Bevande"), ("en:salty-snacks", "Snack"), ("en:snacks", "Snack"), ("en:flours", "Farine"),
    ("en:sugars", "Zucchero"), ("en:fruits", "Frutta e verdura"), ("en:vegetables", "Frutta e verdura"),
    ("en:cereals-and-their-products", "Cereali"),
]


def off_product(code):
    """Ritorna {"found": bool, ...}; usa la cache nel database. Solleva OSError se Open Food Facts non risponde."""
    now = int(time.time())
    with _lock:
        r = db().execute("SELECT data, fetched FROM off_cache WHERE code = ?", (code,)).fetchone()
    if r:
        data = json.loads(r["data"])
        if now - r["fetched"] < OFF_TTL[data["found"]]:
            return data
    req = urllib.request.Request(OFF_URL.format(code), headers={"User-Agent": OFF_UA})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code != 404:          # 404 = prodotto non presente: va in cache come "non trovato"
            raise
        raw = {}
    p = raw.get("product") or {}
    name = (p.get("product_name_it") or p.get("product_name") or "").strip()
    data = {"found": bool(raw.get("status") == 1 and name)}
    if data["found"]:
        brand = (p.get("brands") or "").split(",")[0].strip()
        qty = (p.get("quantity") or "").strip()
        full = name
        if brand and brand.lower() not in name.lower():
            full += " " + brand
        if qty and qty.lower() not in full.lower():
            full += " " + qty
        tags = set(p.get("categories_tags") or [])
        cat = next((it for tag, it in OFF_CATS if tag in tags), "")
        data.update(name=full[:200], brand=brand[:80], quantity=qty[:40], cat=cat)
    with _lock:
        db().execute("INSERT OR REPLACE INTO off_cache VALUES (?,?,?)", (code, json.dumps(data), now))
        db().commit()
    return data


def parse_date_answer(text):
    a, b = text.find("{"), text.rfind("}")
    data = json.loads(text[a:b + 1])
    d = data.get("data")
    try:
        d = datetime.date.fromisoformat(str(d)).isoformat() if d else None
    except ValueError:
        d = None
    return {"data": d, "letto": str(data.get("letto") or "")[:80]}


def ask_ai(prompt, image_b64=None):
    content = prompt if not image_b64 else [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
        {"type": "text", "text": prompt}]
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": AI_MODEL, "max_tokens": 2500,
                         "messages": [{"role": "user", "content": content}]}).encode(),
        headers={"x-api-key": ai_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read())
    return "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")

# ---------- aggiornamenti da GitHub ----------
UPDATE_LOG = os.path.join(BASE, "update.log")
UPDATE_EVERY = 10 * 60           # controllo automatico (GitHub concede 60 richieste/ora senza token)
UPDATE_STALE = 3 * 60            # quando la proprietaria apre l'app, ricontrolla se l'ultimo controllo è più vecchio
_upd = {"latest": None, "checked": None, "error": None, "proc": None, "checking": False}
_upd_lock = threading.Lock()


def installed_version():
    """Legge VERSION scritto da update.py: '<sha> <data>' oppure 'zip <data>', poi il messaggio."""
    try:
        with open(os.path.join(BASE, "VERSION")) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    first = lines[0].split() if lines else []
    msg = lines[1].strip() if len(lines) > 1 else ""
    if not first:
        return {"sha": None, "label": "sconosciuta", "message": ""}
    if first[0] == "zip":
        return {"sha": None, "label": "da zip" + (f" del {first[1]}" if len(first) > 1 else ""), "message": msg}
    return {"sha": first[0], "label": first[0][:7], "message": msg}


def check_updates():
    try:
        head = updater.get(f"https://api.github.com/repos/{updater.REPO}/commits/{updater.BRANCH}")
        c = head["commit"]
        latest = {"sha": head["sha"], "message": c["message"].splitlines()[0][:200], "date": c["committer"]["date"]}
        err = None
    except urllib.error.HTTPError as e:
        latest, err = None, f"GitHub ha risposto {e.code}" + (" (repository privato? serve github-token.txt)" if e.code == 404 else "")
    except Exception:
        latest, err = None, "GitHub non raggiungibile: riprova più tardi"
    with _upd_lock:
        if latest:
            _upd["latest"] = latest
        _upd["error"], _upd["checked"] = err, int(time.time() * 1000)
    if latest and latest["sha"] != installed_version()["sha"]:
        try:
            notify_update(latest)
        except Exception as e:
            print("notify_update:", e, flush=True)


def meta_get(key):
    r = db().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def meta_set(key, value):
    db().execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def notify_update(latest):
    """Una sola notifica push per versione, solo alla proprietaria. Il testo lo legge poi il service
    worker da /api/alerts (update_pending), come per gli avvisi di scadenza."""
    with _lock:
        if meta_get("update_notified") == latest["sha"]:
            return
        meta_set("update_notified", latest["sha"])
        meta_set("update_pending", latest["sha"])
        db().commit()
        oid = owner_id()
        if oid:
            push_user(oid)


def update_alert(user_id):
    """Se c'è una notifica di aggiornamento da consegnare alla proprietaria, la restituisce (una volta)."""
    pending = meta_get("update_pending")
    if not pending or user_id != owner_id():
        return None
    meta_set("update_pending", "")
    db().commit()
    with _upd_lock:
        latest = _upd["latest"]
    if not latest or latest["sha"] != pending or pending == installed_version()["sha"]:
        return None
    return {"count": 1, "kind": "update", "tag": "aggiornamento", "title": "Nuova versione della Dispensa",
            "body": (latest["message"] + " · " if latest.get("message") else "") + "Apri l'app per aggiornare."}


def check_if_stale(wait=5):
    """Ricontrolla GitHub se l'ultimo controllo è vecchio; aspetta al massimo `wait` secondi."""
    with _upd_lock:
        fresh = _upd["checked"] and time.time() * 1000 - _upd["checked"] < UPDATE_STALE * 1000
        if fresh or _upd["checking"]:
            return
        _upd["checking"] = True

    def run():
        try:
            check_updates()
        finally:
            with _upd_lock:
                _upd["checking"] = False
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(wait)


def update_loop():
    time.sleep(30)
    while True:
        check_updates()
        time.sleep(UPDATE_EVERY)


def update_log_tail(n=3):
    try:
        with open(UPDATE_LOG) as f:
            lines = [l.strip() for l in f if l.strip()]
        return " · ".join(lines[-n:]) or "nessun dettaglio"
    except OSError:
        return "nessun dettaglio"


def update_status():
    cur = installed_version()
    with _upd_lock:
        latest, proc = _upd["latest"], _upd["proc"]
        code = proc.poll() if proc else None
        st = {"installed": cur, "latest": latest, "checked": _upd["checked"], "error": _upd["error"],
              "available": bool(latest and latest["sha"] != cur["sha"]),
              "running": bool(proc and code is None),
              "failed": update_log_tail() if proc and code not in (None, 0) else None}
    return st


def start_update():
    """Lancia update.py in una sessione separata: sopravvive al riavvio del server che lui stesso fa."""
    with _upd_lock:
        if _upd["proc"] and _upd["proc"].poll() is None:
            return False
        with open(UPDATE_LOG, "w") as log:
            _upd["proc"] = subprocess.Popen([sys.executable, "-u", os.path.join(BASE, "update.py")], cwd=BASE,
                                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True)
    return True


_lock = threading.Lock()
_db = None


def db():
    global _db
    if _db is None:
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                created INTEGER NOT NULL, last_seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS spaces(
                id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, ord INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS recipes(
                id TEXT PRIMARY KEY, data TEXT NOT NULL, created INTEGER NOT NULL, saved_by TEXT);
            CREATE TABLE IF NOT EXISTS push_subs(
                endpoint TEXT PRIMARY KEY, user_id INTEGER NOT NULL, origin TEXT, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS push_prefs(
                user_id INTEGER PRIMARY KEY, days INTEGER NOT NULL, hour INTEGER NOT NULL, last_sent TEXT);
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS off_cache(
                code TEXT PRIMARY KEY, data TEXT NOT NULL, fetched INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS items(
                id TEXT PRIMARY KEY, data TEXT NOT NULL,
                updated INTEGER NOT NULL, updated_by TEXT);
            CREATE TABLE IF NOT EXISTS shopping(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, cat TEXT NOT NULL DEFAULT '',
                code TEXT NOT NULL DEFAULT '', qty INTEGER NOT NULL DEFAULT 1,
                done INTEGER NOT NULL DEFAULT 0, space TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL,
                updated INTEGER NOT NULL, added_by TEXT);
            CREATE TABLE IF NOT EXISTS shop_ai(
                store TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, created INTEGER NOT NULL,
                PRIMARY KEY(store, key));
        """)
        if not _db.execute("SELECT 1 FROM spaces").fetchone():
            _db.executemany("INSERT INTO spaces VALUES (?,?,?)", [("dispensa", "Dispensa", 0), ("frigo", "Frigo", 1)])
            _db.commit()
    return _db


def list_spaces():
    return [dict(r) for r in db().execute("SELECT id, name FROM spaces ORDER BY ord, rowid")]


MAX_USERS = 10


def seed_users(names):
    """Crea i nomi iniziali se mancano; non cancella mai quelli aggiunti dall'app."""
    with _lock:
        for n in names:
            db().execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (n,))
        db().commit()


def clean_name(v):
    n = " ".join(str(v or "").split())[:40]
    if not n:
        raise ValueError("Scrivi un nome")
    return n


def clean_item(body):
    it = {k: body.get(k) for k in FIELDS}
    it["name"] = str(it["name"] or "").strip()[:200]
    if not it["name"]:
        raise ValueError("Il nome è obbligatorio")
    try:
        it["qty"] = max(0, int(float(it["qty"] or 0)))
    except (TypeError, ValueError):
        it["qty"] = 0
    for k in ("code", "space", "exp", "cat"):
        it[k] = str(it[k] or "").strip()[:100]
    it["note"] = str(it["note"] or "").strip()[:2000]
    return it


# ---------- lista della spesa ----------
MAX_SHOPPING = 300


def shop_out(r):
    return {"id": r["id"], "name": r["name"], "cat": r["cat"], "code": r["code"], "qty": r["qty"],
            "done": bool(r["done"]), "space": r["space"], "item": r["item_id"],
            "created": r["created"], "updated": r["updated"], "addedBy": r["added_by"]}


def shop_list():
    rows = db().execute("SELECT * FROM shopping ORDER BY done, created").fetchall()
    return [shop_out(r) for r in rows]


def shop_add(name, cat="", code="", qty=1, space="", item_id="", user=""):
    """Aggiunge una voce senza fare commit (chi chiama decide). Stesso codice a barre (o, senza codice,
    stesso nome) di una voce non ancora comprata: aumenta la quantità invece di creare un doppione.
    Restituisce (voce, creata)."""
    name = " ".join(str(name or "").split())[:200]
    if not name:
        raise ValueError("Scrivi cosa comprare")
    code = str(code or "").strip()[:100]
    cat, space, item_id = (str(v or "").strip()[:100] for v in (cat, space, item_id))
    try:
        qty = max(1, min(99, int(float(qty or 1))))
    except (TypeError, ValueError):
        qty = 1
    now = int(time.time() * 1000)
    same = None
    for r in db().execute("SELECT * FROM shopping WHERE done = 0"):
        if (code and r["code"] == code) or (not code and r["name"].casefold() == name.casefold()):
            same = r
            break
    if same:
        db().execute("UPDATE shopping SET qty = MIN(99, qty + ?), updated = ?, "
                     "cat = CASE WHEN cat = '' THEN ? ELSE cat END, space = CASE WHEN space = '' THEN ? ELSE space END, "
                     "item_id = CASE WHEN ? != '' THEN ? ELSE item_id END WHERE id = ?",
                     (qty, now, cat, space, item_id, item_id, same["id"]))
        return shop_out(db().execute("SELECT * FROM shopping WHERE id = ?", (same["id"],)).fetchone()), False
    if db().execute("SELECT COUNT(*) FROM shopping").fetchone()[0] >= MAX_SHOPPING:
        raise ValueError(f"La lista ha già {MAX_SHOPPING} voci: togli quelle comprate")
    sid = "p" + secrets.token_hex(6)
    db().execute("INSERT INTO shopping(id, name, cat, code, qty, done, space, item_id, created, updated, added_by) "
                 "VALUES (?,?,?,?,?,0,?,?,?,?,?)", (sid, name, cat, code, qty, space, item_id, now, now, user))
    return shop_out(db().execute("SELECT * FROM shopping WHERE id = ?", (sid,)).fetchone()), True


# ---------- dove si compra (fase 2): stima dell'AI per negozio, in cache ----------
MAX_STORES = 8
WHERE_TTL = 90 * 24 * 3600
WHERE_BATCH = 40


def shop_stores():
    try:
        v = json.loads(meta_get("shop_stores") or "[]")
        return [str(x) for x in v][:MAX_STORES] if isinstance(v, list) else []
    except ValueError:
        return []


def where_key(name):
    return " ".join(str(name or "").casefold().split())


def where_cached(store, entries):
    """{id voce: stima} per le voci con una stima ancora valida."""
    limit = int(time.time()) - WHERE_TTL
    out = {}
    for e in entries:
        r = db().execute("SELECT data FROM shop_ai WHERE store = ? AND key = ? AND created > ?",
                         (store.casefold(), where_key(e["name"]), limit)).fetchone()
        if r:
            out[e["id"]] = json.loads(r["data"])
    return out


def where_all(entries):
    todo = [e for e in entries if not e["done"]]
    return {st: where_cached(st, todo) for st in shop_stores()}


def where_prompt(store, names, others):
    lines = "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1))
    return f"""Conosci bene i supermercati italiani. Negozio: {store}.
Per ogni prodotto della lista dimmi se DI NORMA si trova in questo negozio, cioè se fa parte dell'assortimento abituale della catena in Italia (non la disponibilità di oggi in un punto vendita: quella non puoi saperla).
Altri negozi che usiamo: {", ".join(others) or "nessuno"}.

Prodotti (come li scriviamo noi):
{lines}

Regole:
- "trovi": true se di solito c'è (anche solo la versione a marchio del negozio va bene: dillo in "nota"); false se di solito non c'è (per esempio una marca che la catena non vende o un prodotto di nicchia).
- Se "trovi" è false: in "alternativa" un prodotto simile che di norma c'è in {store} (per esempio quello a marchio del negozio) e in "altrove" il nome di uno degli altri negozi che usiamo dove di norma si trova ("" se nessuno).
- "nota": al massimo una frase breve, facoltativa (marca del negozio, reparto…).
- "certo": false se non sei sicuro.

Rispondi SOLO con JSON valido, senza testo prima o dopo:
{{"prodotti":[{{"n":1,"trovi":true,"certo":true,"nota":"","alternativa":"","altrove":""}}]}}"""


def parse_where(text, n):
    a, b = text.find("{"), text.rfind("}")
    data = json.loads(text[a:b + 1])
    out = {}
    for p in data.get("prodotti", []):
        try:
            i = int(p.get("n"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= n:
            out[i] = {"trovi": bool(p.get("trovi")), "certo": p.get("certo") is not False,
                      "nota": str(p.get("nota") or "")[:160], "alternativa": str(p.get("alternativa") or "")[:120],
                      "altrove": str(p.get("altrove") or "")[:40]}
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Dispensa/1"

    # ---------- utilità ----------
    def log_message(self, fmt, *args):
        if os.environ.get("DISPENSA_LOG"):
            super().log_message(fmt, *args)

    def send_json(self, status, payload, extra_headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, limit=64 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("Richiesta troppo grande")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON non valido")
        return data

    def is_https(self):
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def cookie_header(self, token, max_age):
        parts = [f"{COOKIE}={token}", "Path=/", f"Max-Age={max_age}", "HttpOnly", "SameSite=Strict"]
        if self.is_https():
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def current_user(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if COOKIE not in jar:
            return None
        token = jar[COOKIE].value
        with _lock:
            row = db().execute(
                "SELECT u.id, u.name FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
                (token,)).fetchone()
            if row:
                db().execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (int(time.time()), token))
                db().commit()
        return dict(row) if row else None

    def need_user(self):
        u = self.current_user()
        if not u:
            self.send_json(401, {"error": "Scegli chi sei"})
        return u

    # ---------- routing ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/users":
            with _lock:
                rows = db().execute("SELECT name FROM users ORDER BY id").fetchall()
            return self.send_json(200, {"users": [r["name"] for r in rows]})
        if path == "/api/me":
            u = self.current_user()
            return self.send_json(200, {"name": u["name"]}) if u else self.send_json(401, {"error": "Scegli chi sei"})
        if path == "/api/items":
            u = self.need_user()
            if not u:
                return
            with _lock:
                rows = db().execute("SELECT id, data, updated, updated_by FROM items").fetchall()
            items = []
            for r in rows:
                it = json.loads(r["data"])
                it.update(id=r["id"], updated=r["updated"], updatedBy=r["updated_by"])
                items.append(it)
            with _lock:
                spaces = list_spaces()
            return self.send_json(200, {"items": items, "spaces": spaces, "ai": bool(engine_for(u)), "now": int(time.time() * 1000)})
        mp = re.match(r"^/api/product/(\d{6,14})$", path)
        if mp:
            if not self.need_user():
                return
            try:
                return self.send_json(200, off_product(mp.group(1)))
            except (OSError, ValueError):
                return self.send_json(502, {"error": "Open Food Facts non raggiungibile"})
        if path == "/api/ai/settings":
            u = self.need_user()
            if not u:
                return
            with _lock:
                oid = owner_id()
                owner = db().execute("SELECT name FROM users WHERE id = ?", (oid,)).fetchone()
            tok, key = claude_token(), ai_key()
            saved = tok.get("saved") if tok else None
            expires = (datetime.date.fromisoformat(saved) + datetime.timedelta(days=365)).isoformat() if saved else None
            return self.send_json(200, {
                "owner": u["id"] == oid, "ownerName": owner["name"] if owner else "",
                "engine": engine_for(u), "apiKey": bool(key), "apiKeyTail": key[-4:] if key else "",
                "claudeInstalled": bool(claude_bin()), "claudeToken": bool(tok),
                "tokenSaved": saved, "tokenExpires": expires})
        if path == "/api/update":
            u = self.need_user()
            if not u:
                return
            with _lock:
                oid = owner_id()
                owner = db().execute("SELECT name FROM users WHERE id = ?", (oid,)).fetchone()
            if u["id"] == oid:
                check_if_stale()
            return self.send_json(200, dict(update_status(), owner=u["id"] == oid, ownerName=owner["name"] if owner else ""))
        if path == "/api/push/key":
            return self.send_json(200, {"key": VAPID[1]})
        if path in ("/api/push/settings", "/api/alerts"):
            u = self.need_user()
            if not u:
                return
            with _lock:
                if path == "/api/alerts":
                    return self.send_json(200, update_alert(u["id"]) or alerts_for(u["id"]))
                row = db().execute("SELECT days, hour FROM push_prefs WHERE user_id = ?", (u["id"],)).fetchone()
                n = db().execute("SELECT COUNT(*) FROM push_subs WHERE user_id = ?", (u["id"],)).fetchone()[0]
            return self.send_json(200, {"days": row["days"] if row else 3, "hour": row["hour"] if row else 9, "devices": n})
        if path == "/api/saved-recipes":
            if not self.need_user():
                return
            with _lock:
                rows = db().execute("SELECT id, data, saved_by FROM recipes ORDER BY created DESC").fetchall()
            return self.send_json(200, {"recipes": [dict(json.loads(r["data"]), id=r["id"], savedBy=r["saved_by"]) for r in rows]})
        if path == "/api/shopping":
            if not self.need_user():
                return
            with _lock:
                lst = shop_list()
                return self.send_json(200, {"items": lst, "stores": shop_stores(), "where": where_all(lst)})
        if path.startswith("/api/"):
            return self.send_json(404, {"error": "Non trovato"})
        return self.serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                name = str(self.read_json().get("name", "")).strip()
                with _lock:
                    row = db().execute("SELECT id, name FROM users WHERE name = ?", (name,)).fetchone()
                    if not row:
                        return self.send_json(400, {"error": "Nome non riconosciuto"})
                    token = secrets.token_urlsafe(32)
                    now = int(time.time())
                    db().execute("INSERT INTO sessions VALUES (?,?,?,?)", (token, row["id"], now, now))
                    db().commit()
                return self.send_json(200, {"name": row["name"]}, [self.cookie_header(token, ONE_YEAR)])
            if path == "/api/users":
                name = clean_name(self.read_json().get("name"))
                with _lock:
                    if db().execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone():
                        return self.send_json(400, {"error": "Questo nome esiste già"})
                    if db().execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_USERS:
                        return self.send_json(400, {"error": f"Massimo {MAX_USERS} persone"})
                    db().execute("INSERT INTO users(name) VALUES (?)", (name,))
                    db().commit()
                return self.send_json(200, {"name": name})
            if path == "/api/spaces":
                if not self.need_user():
                    return
                name = clean_name(self.read_json().get("name"))
                with _lock:
                    if db().execute("SELECT 1 FROM spaces WHERE name = ?", (name,)).fetchone():
                        return self.send_json(400, {"error": "Questo ambiente esiste già"})
                    if db().execute("SELECT COUNT(*) FROM spaces").fetchone()[0] >= MAX_SPACES:
                        return self.send_json(400, {"error": f"Massimo {MAX_SPACES} ambienti"})
                    sid = "s" + secrets.token_hex(4)
                    ordv = db().execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM spaces").fetchone()[0]
                    db().execute("INSERT INTO spaces VALUES (?,?,?)", (sid, name, ordv))
                    db().commit()
                return self.send_json(200, {"id": sid, "name": name})
            if path.startswith("/api/push/"):
                u = self.need_user()
                if not u:
                    return
                body = self.read_json()
                if path == "/api/push/settings":
                    days, hour = clean_prefs(body)
                    with _lock:
                        db().execute("INSERT INTO push_prefs(user_id, days, hour) VALUES (?,?,?) "
                                     "ON CONFLICT(user_id) DO UPDATE SET days=excluded.days, hour=excluded.hour, last_sent=NULL",
                                     (u["id"], days, hour))
                        db().commit()
                    return self.send_json(200, {"days": days, "hour": hour})
                if path == "/api/push/subscribe":
                    ep = str((body.get("subscription") or {}).get("endpoint") or "")
                    if not ep.startswith("https://") or len(ep) > 1000:
                        return self.send_json(400, {"error": "Iscrizione non valida"})
                    origin = self.headers.get("Origin") or ("https://" + (self.headers.get("Host") or "localhost"))
                    days, hour = clean_prefs(body)
                    with _lock:
                        db().execute("INSERT OR REPLACE INTO push_subs VALUES (?,?,?,?)", (ep, u["id"], origin, int(time.time())))
                        db().execute("INSERT INTO push_prefs(user_id, days, hour) VALUES (?,?,?) "
                                     "ON CONFLICT(user_id) DO UPDATE SET days=excluded.days, hour=excluded.hour", (u["id"], days, hour))
                        db().commit()
                    return self.send_json(200, {"ok": True})
                if path == "/api/push/unsubscribe":
                    with _lock:
                        db().execute("DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?", (str(body.get("endpoint") or ""), u["id"]))
                        db().commit()
                    return self.send_json(200, {"ok": True})
                if path == "/api/push/test":
                    with _lock:
                        sent, errors = push_user(u["id"])
                    if not sent:
                        return self.send_json(502, {"error": "Nessuna notifica partita" + (f": {errors[0]}" if errors else ": attiva prima le notifiche su questo telefono")})
                    return self.send_json(200, {"sent": sent})
                return self.send_json(404, {"error": "Non trovato"})
            if path.startswith("/api/ai/"):
                u = self.need_user()
                if not u:
                    return
                with _lock:
                    is_owner = u["id"] == owner_id()
                if not is_owner:
                    return self.send_json(403, {"error": "Solo la proprietaria dell'app può cambiare queste impostazioni"})
                body = self.read_json()
                if path == "/api/ai/claude-token":
                    t = str(body.get("token") or "").strip()
                    if not t.startswith("sk-ant-") or len(t) < 30 or len(t) > 400 or any(c.isspace() for c in t):
                        return self.send_json(400, {"error": "Non sembra un token di claude setup-token (inizia con sk-ant-)"})
                    save_secret(CLAUDE_TOKEN_FILE, json.dumps({"token": t, "saved": datetime.date.today().isoformat()}))
                    return self.send_json(200, {"ok": True})
                if path == "/api/ai/api-key":
                    k = str(body.get("key") or "").strip()
                    if not k.startswith("sk-ant-") or len(k) < 30 or any(c.isspace() for c in k):
                        return self.send_json(400, {"error": "Non sembra una chiave API Anthropic (inizia con sk-ant-)"})
                    save_secret(AI_KEY_FILE, k)
                    return self.send_json(200, {"ok": True})
                if path == "/api/ai/forget":
                    what = body.get("what")
                    target = CLAUDE_TOKEN_FILE if what == "claude" else AI_KEY_FILE if what == "api" else None
                    if target and os.path.exists(target):
                        os.remove(target)
                    return self.send_json(200, {"ok": True})
                if path == "/api/ai/test":
                    try:
                        out = generate(u, "Rispondi soltanto con la parola OK.")
                    except AIError as e:
                        return self.send_json(502, {"error": str(e)})
                    return self.send_json(200, {"ok": True, "engine": engine_for(u), "reply": out.strip()[:40]})
                return self.send_json(404, {"error": "Non trovato"})
            if path.startswith("/api/update/"):
                u = self.need_user()
                if not u:
                    return
                with _lock:
                    is_owner = u["id"] == owner_id()
                if not is_owner:
                    return self.send_json(403, {"error": "Solo la proprietaria dell'app può installare gli aggiornamenti"})
                if path == "/api/update/check":
                    with _upd_lock:
                        recent = _upd["checked"] and time.time() * 1000 - _upd["checked"] < 15000
                    if not recent:
                        check_updates()
                    return self.send_json(200, update_status())
                if path == "/api/update/run":
                    if not start_update():
                        return self.send_json(409, {"error": "Aggiornamento già in corso"})
                    return self.send_json(202, {"ok": True})
                return self.send_json(404, {"error": "Non trovato"})
            if path == "/api/read-date":
                u = self.need_user()
                if not u:
                    return
                if not engine_for(u):
                    return self.send_json(400, {"error": "Lettura non attiva: collega l'AI in Menu › Intelligenza artificiale"})
                img = str(self.read_json(limit=6 * 1024 * 1024).get("image") or "")
                if "," in img[:100]:
                    img = img.split(",", 1)[1]
                if len(img) < 100:
                    return self.send_json(400, {"error": "Foto mancante"})
                try:
                    return self.send_json(200, parse_date_answer(generate(u, DATE_PROMPT.format(today=datetime.date.today().isoformat()), img)))
                except AIError as e:
                    return self.send_json(502, {"error": str(e)})
                except (ValueError, KeyError):
                    return self.send_json(502, {"error": "Data non leggibile: riprova la foto"})
            if path == "/api/recognize":
                u = self.need_user()
                if not u:
                    return
                if not engine_for(u):
                    return self.send_json(400, {"error": "Riconoscimento non attivo: collega l'AI in Menu › Intelligenza artificiale"})
                img = str(self.read_json(limit=6 * 1024 * 1024).get("image") or "")
                if "," in img[:100]:
                    img = img.split(",", 1)[1]
                if len(img) < 100:
                    return self.send_json(400, {"error": "Foto mancante"})
                with _lock:
                    rows = db().execute("SELECT data FROM items").fetchall()
                cats = sorted({(json.loads(r["data"]).get("cat") or "").strip() for r in rows} - {""})[:40]
                prompt = RECOGNIZE_PROMPT.format(today=datetime.date.today().isoformat(),
                                                 cats=", ".join(cats) or "nessuna, scegline tu una")
                try:
                    return self.send_json(200, parse_recognize_answer(generate(u, prompt, img)))
                except AIError as e:
                    return self.send_json(502, {"error": str(e)})
                except (ValueError, KeyError):
                    return self.send_json(502, {"error": "Prodotto non riconosciuto: riprova la foto"})
            if path == "/api/recipes":
                u = self.need_user()
                if not u:
                    return
                if not engine_for(u):
                    return self.send_json(400, {"error": "Ricette non attive: collega l'AI in Menu › Intelligenza artificiale"})
                body = self.read_json()
                with _lock:
                    rows = db().execute("SELECT id, data FROM items").fetchall()
                every = [dict(json.loads(r["data"]), id=r["id"]) for r in rows]
                scelti = body.get("scelti")
                if isinstance(scelti, list):          # scheda Ricette: prodotti spuntati dalla persona
                    ids = {str(x) for x in scelti[:40]}
                    usable = [it for it in every if int(it.get("qty") or 0) > 0 and (days_left(it.get("exp")) or 0) >= 0]
                    chosen = [it for it in usable if it["id"] in ids]
                    if not chosen:
                        return self.send_json(400, {"error": "Spunta almeno un prodotto disponibile"})
                    prompt = pick_prompt(chosen, [it for it in usable if it["id"] not in ids], str(body.get("richiesta") or ""))
                else:                                 # idee per cosa scade
                    prompt, n = recipe_prompt(every)
                    if not n:
                        return self.send_json(400, {"error": "Nessun prodotto in scadenza nei prossimi 7 giorni"})
                try:
                    return self.send_json(200, {"ricette": parse_recipes(generate(u, prompt))})
                except AIError as e:
                    return self.send_json(502, {"error": str(e)})
                except (ValueError, KeyError):
                    return self.send_json(502, {"error": "Risposta AI non leggibile: riprova"})
            if path == "/api/shopping":
                u = self.need_user()
                if not u:
                    return
                b = self.read_json()
                with _lock:
                    try:
                        entry, created = shop_add(b.get("name"), b.get("cat"), b.get("code"), b.get("qty") or 1,
                                                  b.get("space"), b.get("item"), u["name"])
                        db().commit()
                    except Exception:
                        db().rollback()
                        raise
                return self.send_json(200, {"entry": entry, "created": created})
            if path == "/api/shop-stores":
                if not self.need_user():
                    return
                raw = self.read_json().get("stores")
                if not isinstance(raw, list):
                    raise ValueError("Elenco dei negozi non valido")
                stores = []
                for x in raw:
                    n = clean_name(x)
                    if n.casefold() not in (s.casefold() for s in stores):
                        stores.append(n)
                if len(stores) > MAX_STORES:
                    raise ValueError(f"Al massimo {MAX_STORES} negozi")
                with _lock:
                    meta_set("shop_stores", json.dumps(stores, ensure_ascii=False))
                    db().commit()
                return self.send_json(200, {"stores": stores})
            if path == "/api/shopping/where":
                u = self.need_user()
                if not u:
                    return
                if not engine_for(u):
                    return self.send_json(400, {"error": "Per la stima serve l'AI: collegala in Menu › Intelligenza artificiale"})
                b = self.read_json()
                store = " ".join(str(b.get("store") or "").split())[:40]
                with _lock:
                    stores = shop_stores()
                    if store.casefold() not in (s.casefold() for s in stores):
                        return self.send_json(400, {"error": "Scegli prima il negozio tra i preferiti"})
                    todo = [e for e in shop_list() if not e["done"]]
                    have = {} if b.get("fresh") else where_cached(store, todo)
                names = []
                for e in todo:                     # un nome solo una volta, anche se ripetuto
                    if e["id"] not in have and where_key(e["name"]) not in (where_key(n) for n in names):
                        names.append(e["name"])
                names = names[:WHERE_BATCH]
                if names:
                    others = [s for s in stores if s.casefold() != store.casefold()]
                    try:
                        got = parse_where(generate(u, where_prompt(store, names, others)), len(names))
                    except AIError as e:
                        return self.send_json(502, {"error": str(e)})
                    except (ValueError, KeyError, AttributeError):
                        return self.send_json(502, {"error": "Risposta AI non leggibile: riprova"})
                    now = int(time.time())
                    with _lock:
                        for i, n in enumerate(names, 1):
                            if i in got:
                                db().execute("INSERT OR REPLACE INTO shop_ai VALUES (?,?,?,?)",
                                             (store.casefold(), where_key(n), json.dumps(got[i], ensure_ascii=False), now))
                        db().commit()
                with _lock:
                    todo = [e for e in shop_list() if not e["done"]]
                    return self.send_json(200, {"where": where_cached(store, todo)})
            mt = re.match(r"^/api/items/([^/]+)/to-shopping$", path)
            if mt:
                u = self.need_user()
                if not u:
                    return
                item_id = mt.group(1)
                now = int(time.time() * 1000)
                with _lock:
                    row = db().execute("SELECT data FROM items WHERE id = ?", (item_id,)).fetchone()
                    if not row:
                        return self.send_json(404, {"error": "Prodotto non trovato: forse l'ha già tolto qualcun altro"})
                    it = json.loads(row["data"])
                    try:
                        # prima in lista, poi via dalla dispensa: tutto nella stessa transazione
                        entry, created = shop_add(it.get("name"), it.get("cat"), it.get("code"), 1,
                                                  it.get("space"), item_id, u["name"])
                        q = int(it.get("qty") or 0)
                        if q > 1:
                            it["qty"] = q - 1
                            db().execute("UPDATE items SET data = ?, updated = ?, updated_by = ? WHERE id = ?",
                                         (json.dumps(it, ensure_ascii=False), now, u["name"], item_id))
                            out = dict(it, id=item_id, updated=now, updatedBy=u["name"])
                        else:
                            db().execute("DELETE FROM items WHERE id = ?", (item_id,))
                            out = None
                        db().commit()
                    except Exception:
                        db().rollback()
                        raise
                return self.send_json(200, {"entry": entry, "created": created, "item": out})
            if path == "/api/logout":
                jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
                if COOKIE in jar:
                    with _lock:
                        db().execute("DELETE FROM sessions WHERE token = ?", (jar[COOKIE].value,))
                        db().commit()
                return self.send_json(200, {"ok": True}, [self.cookie_header("", 0)])
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})
        return self.send_json(404, {"error": "Non trovato"})

    def do_PATCH(self):
        msh = re.match(r"^/api/shopping/([^/]+)$", urlparse(self.path).path)
        if msh:
            if not self.need_user():
                return
            try:
                body = self.read_json()
                sets, vals = [], []
                if "qty" in body:
                    try:
                        q = int(float(body["qty"] or 1))
                    except (TypeError, ValueError):
                        raise ValueError("Quantità non valida")
                    sets.append("qty = ?"); vals.append(max(1, min(99, q)))
                if "done" in body:
                    sets.append("done = ?"); vals.append(1 if body["done"] else 0)
                if "name" in body:
                    n = " ".join(str(body["name"] or "").split())[:200]
                    if not n:
                        raise ValueError("Scrivi cosa comprare")
                    sets.append("name = ?"); vals.append(n)
                if "cat" in body:
                    sets.append("cat = ?"); vals.append(str(body["cat"] or "").strip()[:100])
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e) or "Dati non validi"})
            with _lock:
                if sets:
                    db().execute(f"UPDATE shopping SET {', '.join(sets)}, updated = ? WHERE id = ?",
                                 (*vals, int(time.time() * 1000), msh.group(1)))
                    db().commit()
                row = db().execute("SELECT * FROM shopping WHERE id = ?", (msh.group(1),)).fetchone()
            if not row:
                return self.send_json(404, {"error": "Voce non trovata: forse l'ha già tolta qualcun altro"})
            return self.send_json(200, {"entry": shop_out(row)})
        if urlparse(self.path).path == "/api/spaces":
            if not self.need_user():
                return
            try:
                body = self.read_json()
                sid, name = str(body.get("id", "")), clean_name(body.get("name"))
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e)})
            with _lock:
                if db().execute("SELECT 1 FROM spaces WHERE name = ? AND id != ?", (name, sid)).fetchone():
                    return self.send_json(400, {"error": "Questo ambiente esiste già"})
                db().execute("UPDATE spaces SET name = ? WHERE id = ?", (name, sid))
                db().commit()
            return self.send_json(200, {"id": sid, "name": name})
        if urlparse(self.path).path != "/api/users":
            return self.send_json(404, {"error": "Non trovato"})
        try:
            body = self.read_json()
            old, new = clean_name(body.get("from")), clean_name(body.get("to"))
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})
        with _lock:
            if not db().execute("SELECT 1 FROM users WHERE name = ?", (old,)).fetchone():
                return self.send_json(400, {"error": "Nome non trovato"})
            if old != new and db().execute("SELECT 1 FROM users WHERE name = ?", (new,)).fetchone():
                return self.send_json(400, {"error": "Questo nome esiste già"})
            db().execute("UPDATE users SET name = ? WHERE name = ?", (new, old))
            db().execute("UPDATE items SET updated_by = ? WHERE updated_by = ?", (new, old))
            db().commit()
        return self.send_json(200, {"name": new})

    def do_PUT(self):
        path = urlparse(self.path).path
        mr = re.match(r"^/api/saved-recipes/([^/]+)$", path)
        if mr:
            u = self.need_user()
            if not u:
                return
            if not ID_RE.match(mr.group(1)):
                return self.send_json(400, {"error": "Id non valido"})
            try:
                r = parse_recipes(json.dumps({"ricette": [self.read_json()]}))
                if not r:
                    raise ValueError("Ricetta senza titolo")
            except (ValueError, json.JSONDecodeError) as e:
                return self.send_json(400, {"error": str(e)})
            with _lock:
                db().execute("INSERT OR REPLACE INTO recipes VALUES (?,?,?,?)",
                             (mr.group(1), json.dumps(r[0], ensure_ascii=False), int(time.time()), u["name"]))
                db().commit()
            return self.send_json(200, dict(r[0], id=mr.group(1), savedBy=u["name"]))
        m = re.match(r"^/api/items/([^/]+)$", path)
        if not m:
            return self.send_json(404, {"error": "Non trovato"})
        u = self.need_user()
        if not u:
            return
        item_id = m.group(1)
        if not ID_RE.match(item_id):
            return self.send_json(400, {"error": "Id non valido"})
        try:
            it = clean_item(self.read_json())
        except (ValueError, json.JSONDecodeError) as e:
            return self.send_json(400, {"error": str(e)})
        now = int(time.time() * 1000)
        with _lock:
            db().execute(
                "INSERT INTO items(id, data, updated, updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated=excluded.updated, updated_by=excluded.updated_by",
                (item_id, json.dumps(it, ensure_ascii=False), now, u["name"]))
            db().commit()
        it.update(id=item_id, updated=now, updatedBy=u["name"])
        return self.send_json(200, {"item": it})

    def do_DELETE(self):
        path = urlparse(self.path).path
        mr = re.match(r"^/api/saved-recipes/([^/]+)$", path)
        if mr:
            if not self.need_user():
                return
            with _lock:
                db().execute("DELETE FROM recipes WHERE id = ?", (mr.group(1),))
                db().commit()
            return self.send_json(200, {"ok": True})
        if path == "/api/shopping" or path.startswith("/api/shopping/"):
            if not self.need_user():
                return
            with _lock:
                if path == "/api/shopping":
                    db().execute("DELETE FROM shopping WHERE done = 1")
                else:
                    db().execute("DELETE FROM shopping WHERE id = ?", (unquote(path[len("/api/shopping/"):]),))
                db().commit()
            return self.send_json(200, {"ok": True})
        ms = re.match(r"^/api/spaces/([^/]+)$", path)
        if ms:
            if not self.need_user():
                return
            sid = unquote(ms.group(1))
            with _lock:
                if db().execute("SELECT COUNT(*) FROM spaces").fetchone()[0] <= 1:
                    return self.send_json(400, {"error": "Serve almeno un ambiente"})
                used = sum(1 for r in db().execute("SELECT data FROM items") if json.loads(r["data"]).get("space") == sid)
                if used:
                    return self.send_json(400, {"error": ("Contiene ancora 1 prodotto: spostalo o eliminalo prima" if used == 1 else f"Contiene ancora {used} prodotti: spostali o eliminali prima")})
                db().execute("DELETE FROM spaces WHERE id = ?", (sid,))
                db().commit()
            return self.send_json(200, {"ok": True})
        mu = re.match(r"^/api/users/([^/]+)$", path)
        if mu:
            name = unquote(mu.group(1))
            with _lock:
                row = db().execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
                if row:
                    db().execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
                    db().execute("DELETE FROM push_subs WHERE user_id = ?", (row["id"],))
                    db().execute("DELETE FROM push_prefs WHERE user_id = ?", (row["id"],))
                    db().execute("DELETE FROM users WHERE id = ?", (row["id"],))
                    db().commit()
            return self.send_json(200, {"ok": True})
        m = re.match(r"^/api/items/([^/]+)$", path)
        if not m:
            return self.send_json(404, {"error": "Non trovato"})
        if not self.need_user():
            return
        with _lock:
            db().execute("DELETE FROM items WHERE id = ?", (m.group(1),))
            db().commit()
        return self.send_json(200, {"ok": True})

    # ---------- file statici ----------
    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(APP_DIR, path.lstrip("/")))
        if not full.startswith(os.path.realpath(APP_DIR) + os.sep) or not os.path.isfile(full):
            full = os.path.join(APP_DIR, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description="Server della Dispensa")
    ap.add_argument("--users", default="", help='Nomi iniziali facoltativi, es. "Aurora"')
    ap.add_argument("--host", default="0.0.0.0", help="Indirizzo di ascolto (default: tutte le interfacce)")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    names = [n.strip() for n in a.users.split(",") if n.strip()]
    seed_users(names)
    global VAPID
    VAPID = webpush.load_keys(VAPID_FILE)
    threading.Thread(target=push_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Dispensa attiva su http://{a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
