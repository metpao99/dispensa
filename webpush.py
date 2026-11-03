"""
Web Push minimo (solo libreria standard): chiavi VAPID su curva P-256, firma ES256
e invio di notifiche SENZA contenuto. Il testo lo prende il service worker
dell'app chiedendolo al NAS (/api/alerts), quindi dal servizio push di Apple
passa solo un "segnale".
"""
import base64, hashlib, json, os, secrets, time, urllib.error, urllib.parse, urllib.request

# --- curva P-256 (NIST) ---
P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
A = P - 3
B = 0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b
N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
     0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def _mul(k, pt=G):
    out = None
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _i2b(i):
    return i.to_bytes(32, "big")


def load_keys(path):
    """Crea (la prima volta) e legge le chiavi VAPID da un file JSON."""
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        priv = int(d["private"], 16)
    else:
        priv = secrets.randbelow(N - 1) + 1
        with open(path, "w") as f:
            json.dump({"private": format(priv, "064x")}, f)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    x, y = _mul(priv)
    return priv, b64u(b"\x04" + _i2b(x) + _i2b(y))


def sign_es256(priv, msg):
    z = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    while True:
        k = secrets.randbelow(N - 1) + 1
        r = _mul(k)[0] % N
        if not r:
            continue
        s = pow(k, -1, N) * (z + r * priv) % N
        if s:
            return _i2b(r) + _i2b(s)


def vapid_header(priv, pub, endpoint, subject):
    u = urllib.parse.urlparse(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                            "sub": subject}, separators=(",", ":")).encode())
    sig = b64u(sign_es256(priv, f"{head}.{body}".encode()))
    return f"vapid t={head}.{body}.{sig}, k={pub}"


def send(priv, pub, endpoint, subject, ttl=86400):
    """Invia un segnale push. Ritorna lo status HTTP (201 = ok, 404/410 = iscrizione scaduta)."""
    req = urllib.request.Request(endpoint, data=b"", method="POST", headers={
        "Authorization": vapid_header(priv, pub, endpoint, subject),
        "TTL": str(ttl), "Urgency": "normal", "Content-Length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
