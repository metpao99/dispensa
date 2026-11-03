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
  POST   /api/recipes        idee di ricette con ciò che scade (serve ai-key.txt)
  GET    /api/saved-recipes  ricettario; PUT/DELETE /api/saved-recipes/<id>
  GET    /api/push/key | /api/push/settings, POST /api/push/settings | subscribe | unsubscribe | test
  GET    /api/alerts         testo dell'avviso per chi chiede (usato dal service worker)
  POST   /api/read-date      {"image": base64 JPEG} -> legge la scadenza dalla foto (serve ai-key.txt)
  PUT    /api/items/<id>     crea/aggiorna un prodotto
  DELETE /api/items/<id>     elimina un prodotto
  GET    /api/update         versione installata e ultima su GitHub (controllo automatico ogni 6 ore)
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
UPDATE_EVERY = 6 * 3600          # controllo automatico (GitHub concede 60 richieste/ora senza token)
_upd = {"latest": None, "checked": None, "error": None, "proc": None}
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
            CREATE TABLE IF NOT EXISTS items(
                id TEXT PRIMARY KEY, data TEXT NOT NULL,
                updated INTEGER NOT NULL, updated_by TEXT);
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
            return self.send_json(200, dict(update_status(), owner=u["id"] == oid, ownerName=owner["name"] if owner else ""))
        if path == "/api/push/key":
            return self.send_json(200, {"key": VAPID[1]})
        if path in ("/api/push/settings", "/api/alerts"):
            u = self.need_user()
            if not u:
                return
            with _lock:
                if path == "/api/alerts":
                    return self.send_json(200, alerts_for(u["id"]))
                row = db().execute("SELECT days, hour FROM push_prefs WHERE user_id = ?", (u["id"],)).fetchone()
                n = db().execute("SELECT COUNT(*) FROM push_subs WHERE user_id = ?", (u["id"],)).fetchone()[0]
            return self.send_json(200, {"days": row["days"] if row else 3, "hour": row["hour"] if row else 9, "devices": n})
        if path == "/api/saved-recipes":
            if not self.need_user():
                return
            with _lock:
                rows = db().execute("SELECT id, data, saved_by FROM recipes ORDER BY created DESC").fetchall()
            return self.send_json(200, {"recipes": [dict(json.loads(r["data"]), id=r["id"], savedBy=r["saved_by"]) for r in rows]})
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
            if path == "/api/recipes":
                u = self.need_user()
                if not u:
                    return
                if not engine_for(u):
                    return self.send_json(400, {"error": "Ricette non attive: collega l'AI in Menu › Intelligenza artificiale"})
                with _lock:
                    rows = db().execute("SELECT data FROM items").fetchall()
                prompt, n = recipe_prompt([json.loads(r["data"]) for r in rows])
                if not n:
                    return self.send_json(400, {"error": "Nessun prodotto in scadenza nei prossimi 7 giorni"})
                try:
                    return self.send_json(200, {"ricette": parse_recipes(generate(u, prompt))})
                except AIError as e:
                    return self.send_json(502, {"error": str(e)})
                except (ValueError, KeyError):
                    return self.send_json(502, {"error": "Risposta AI non leggibile: riprova"})
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
