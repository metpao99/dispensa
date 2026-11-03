#!/usr/bin/env python3
"""
Aggiorna la Dispensa dal repository GitHub (solo libreria standard, niente git).

    python3 update.py            scarica il ramo main, fa la copia del database, aggiorna e riavvia
    python3 update.py --check    dice solo se c'è una versione nuova
    python3 update.py --zip FILE aggiorna da uno zip caricato sul NAS (senza GitHub)

Non tocca mai i dati: dispensa.db, vapid.json, ai-key.txt, github-token.txt, backups/, log.
Repository privato: metti un token GitHub di sola lettura nel file github-token.txt.
"""
import argparse, io, json, zipfile, os, shutil, sqlite3, subprocess, sys, tarfile, time, urllib.error, urllib.request

REPO = "metpao99/dispensa"
BRANCH = "main"
BASE = os.path.dirname(os.path.abspath(__file__))
PROTECTED = {"dispensa.db", "dispensa.db-wal", "dispensa.db-shm", "vapid.json", "ai-key.txt",
             "github-token.txt", "claude-token.json", "backups", "server.log", "server.pid", "VERSION",
             "tailscaled.log", "tailscaled.pid", "state", "tailscale", "tailscaled"}
KEEP_BACKUPS = 10


def token():
    try:
        with open(os.path.join(BASE, "github-token.txt")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def get(url, raw=False):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "dispensa-update"}
    if token():
        h["Authorization"] = "Bearer " + token()
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)


def current_version():
    try:
        with open(os.path.join(BASE, "VERSION")) as f:
            return f.read().split()[0]
    except (OSError, IndexError):
        return None


def backup_db():
    src = os.path.join(BASE, "dispensa.db")
    if not os.path.exists(src):
        return None
    os.makedirs(os.path.join(BASE, "backups"), exist_ok=True)
    dst = os.path.join(BASE, "backups", time.strftime("dispensa-%Y%m%d-%H%M%S.db"))
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:   # copia coerente anche col server acceso
        a.backup(b)
    olds = sorted(f for f in os.listdir(os.path.join(BASE, "backups")) if f.endswith(".db"))
    for f in olds[:-KEEP_BACKUPS]:
        os.remove(os.path.join(BASE, "backups", f))
    return dst


def is_protected(rel):
    parts = rel.split("/")
    if parts[0] in PROTECTED:
        # la cartella tailscale/ contiene sia script (da aggiornare) sia dati: proteggi solo i dati
        if parts[0] == "tailscale" and len(parts) == 2 and parts[1].endswith(".sh"):
            return False
        return True
    if parts[0] == "tailscale" and len(parts) > 1 and parts[1] in PROTECTED:
        return True
    return False


def install(tar_bytes):
    written = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as t:
        for m in t.getmembers():
            rel = m.name.split("/", 1)[1] if "/" in m.name else ""
            if not rel or not m.isfile() or is_protected(rel):
                continue
            if rel.startswith("/") or ".." in rel.split("/"):
                continue
            dst = os.path.join(BASE, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".new"
            with t.extractfile(m) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(tmp, dst)
            if rel.endswith((".sh", ".py")):
                os.chmod(dst, 0o755)
            written += 1
    return written


def install_zip(path):
    """Stesse regole di install(), ma da uno zip (con o senza cartella principale)."""
    written = 0
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        for n in names:
            rel = n.split("/", 1)[1] if strip else n
            if not rel or is_protected(rel) or rel.startswith("/") or ".." in rel.split("/"):
                continue
            dst = os.path.join(BASE, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(n) as src, open(dst + ".new", "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(dst + ".new", dst)
            if rel.endswith((".sh", ".py")):
                os.chmod(dst, 0o755)
            written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description="Aggiorna la Dispensa da GitHub")
    ap.add_argument("--check", action="store_true", help="controlla soltanto")
    ap.add_argument("--no-restart", action="store_true", help="non riavviare il server")
    ap.add_argument("--zip", help="aggiorna da questo file zip invece che da GitHub")
    a = ap.parse_args()
    if a.zip:
        if not zipfile.is_zipfile(a.zip):
            sys.exit(f"Non trovo uno zip valido in {a.zip}")
        print("Copia del database:", backup_db() or "nessun database ancora")
        n = install_zip(a.zip)
        with open(os.path.join(BASE, "VERSION"), "w") as f:
            f.write(f"zip {time.strftime('%Y-%m-%d %H:%M')}\n{os.path.basename(a.zip)}\n")
        print(f"Aggiornati {n} file.")
        if not a.no_restart:
            subprocess.run(["sh", os.path.join(BASE, "start.sh")], check=False)
        print("Fatto. Sui telefoni: chiudi e riapri l'app.")
        return
    try:
        head = get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    except urllib.error.HTTPError as e:
        hint = " (repository privato? metti il token in github-token.txt)" if e.code == 404 else ""
        sys.exit(f"GitHub ha risposto {e.code}{hint}")
    except urllib.error.URLError as e:
        sys.exit(f"GitHub non raggiungibile: {e.reason}")
    sha, msg = head["sha"], head["commit"]["message"].splitlines()[0]
    cur = current_version()
    if cur == sha:
        print(f"Già aggiornata ({sha[:7]}: {msg})")
        return
    print(f"Versione installata: {cur[:7] if cur else 'sconosciuta'}")
    print(f"Versione disponibile: {sha[:7]}: {msg}")
    if a.check:
        return
    b = backup_db()
    print("Copia del database:", b or "nessun database ancora")
    n = install(get(f"https://api.github.com/repos/{REPO}/tarball/{sha}", raw=True))
    with open(os.path.join(BASE, "VERSION"), "w") as f:
        f.write(f"{sha} {time.strftime('%Y-%m-%d %H:%M')}\n{msg}\n")
    print(f"Aggiornati {n} file.")
    if not a.no_restart:
        subprocess.run(["sh", os.path.join(BASE, "start.sh")], check=False)
    print("Fatto. Sui telefoni: chiudi e riapri l'app.")


if __name__ == "__main__":
    main()
