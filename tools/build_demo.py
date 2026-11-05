"""Genera la versione di prova (artifact) dall'app reale in dispensa-server/app.
Il server è simulato nel browser con localStorage; la UI è identica."""
import re

import os, sys
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app") + "/"
OUT = sys.argv[1] if len(sys.argv) > 1 else "dispensa-prova.html"

html = open(APP + "index.html").read()
js = open(APP + "app.js").read()

def rep(s, a, b):
    assert a in s, a[:80]
    return s.replace(a, b, 1)

# --- head: solo title + style; niente manifest/sw/icone ---
style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
dark = re.search(r"@media \(prefers-color-scheme:dark\)\{:root\{(.*?)\}\}", style, re.S).group(1)
style = style.replace(f"@media (prefers-color-scheme:dark){{:root{{{dark}}}}}",
    f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{dark}}}}}\n:root[data-theme="dark"]{{{dark}}}')
style += """
.demo{margin:10px 0 0;padding:8px 12px;border-radius:8px;background:var(--ok-bg);color:var(--fg);font-size:.82rem;display:flex;gap:10px;align-items:center;justify-content:space-between}
.demo button{flex:none;font:600 .8rem var(--sans);border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:5px 9px}
"""
body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
body = rep(body, '<script src="zxing.min.js"></script>', '<script src="https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js"></script>')
body = rep(body, '<script src="app.js"></script>', "<script>\n__APP__\n</script>")
body = rep(body, '  <p class="sync" id="sync" hidden></p>',
    '  <div class="demo"><span>Versione di prova: i dati restano su questo telefono.</span><button type="button" id="demoReset">Azzera prova</button></div>\n  <p class="sync" id="sync" hidden></p>')

MOCK = r'''
/* ---------- server simulato (solo versione di prova) ---------- */
const DEMO_KEY = "dispensa-prova-db", SESS_KEY = "dispensa-prova-sessione";
function demoSeed(){
  const now = Date.now(), d = n => new Date(now + n * 864e5).toISOString().slice(0,10);
  const it = (id, space, name, cat, qty, exp, note) => [id, { code:"", name, space, qty, exp, cat, note, updated: now, updatedBy: "esempio" }];
  return { users: [], sessions: {}, recipes: {}, spaces: [{id:"dispensa",name:"Dispensa"},{id:"frigo",name:"Frigo"}],
    items: Object.fromEntries([
      it("es1","dispensa","Pelati 400 g (esempio)","Conserve",3,d(200),""),
      it("es2","dispensa","Fusilli 500 g (esempio)","Pasta",1,d(5),"ricomprare"),
      it("es3","dispensa","Ceci in scatola (esempio)","Conserve",0,"",""),
      it("es4","frigo","Yogurt bianco (esempio)","Latticini",2,d(2),""),
      it("es5","frigo","Burro (esempio)","Latticini",1,d(30),"aperto")
    ]) };
}
const demoSampleP = (async () => { try { return window.claude && window.claude.use ? await window.claude.use("sample") : null; } catch { return null; } })();
function demoPrompt(list){
  const soon = [], other = [];
  for (const it of list){
    if ((parseInt(it.qty) || 0) <= 0) continue;
    const d = daysLeft(it.exp);
    if (d !== null && d < 0) continue;
    if (d !== null && d <= 7) soon.push([d, it]); else other.push(it);
  }
  soon.sort((a, b) => a[0] - b[0]);
  const ls1 = soon.map(([d, it]) => `- ${it.name} · scade tra ${d} giorni · quantità ${it.qty}`).join("\n");
  const ls2 = other.slice(0, 60).map(it => `- ${it.name}`).join("\n") || "- (nient'altro)";
  return [`Sei un aiuto in cucina per una casa italiana. Proponi 3 ricette casalinghe per 2 persone che consumino prima i prodotti in scadenza.

Prodotti in scadenza (nome come lo scriviamo noi · giorni alla scadenza · quantità):
${ls1}

Altri prodotti disponibili in casa:
${ls2}

Regole: ogni ricetta usa almeno un prodotto in scadenza, preferendo quelli più vicini alla scadenza; usa anche gli altri prodotti di casa quando ha senso; dai per scontati sale, pepe, olio, aceto e acqua; elenca solo gli ingredienti che mancano davvero. Passi brevi e pratici.

Rispondi SOLO con JSON valido, senza testo prima o dopo, in questa forma:
{"ricette":[{"titolo":"...","tempo":"25 min","perche":"una frase su cosa salva dalla scadenza","usa":["nomi esatti dei prodotti di casa usati"],"mancano":["ingredienti da comprare"],"passi":["...","..."]}]}`, soon.length];
}
function demoClean(r){
  const a = (v, n) => (Array.isArray(v) ? v : []).slice(0, n).map(x => String(x).slice(0, 500));
  return { titolo: String(r.titolo || "").slice(0, 120), tempo: String(r.tempo || "").slice(0, 30), perche: String(r.perche || "").slice(0, 300),
    usa: a(r.usa, 12), mancano: a(r.mancano, 12), passi: a(r.passi, 15) };
}
function demoLoad(){ const v = ls.get(DEMO_KEY, null); if (v) return v; const s = demoSeed(); ls.set(DEMO_KEY, s); return s; }
function demoErr(status, msg){ const e = status === 401 ? new AuthError() : new Error(msg); e.status = status; throw e; }
async function api(method, path, body, blob){
  await new Promise(r => setTimeout(r, 60));
  const db = demoLoad(), save = () => ls.set(DEMO_KEY, db);
  const who = () => { const t = ls.get(SESS_KEY, null); return t && db.sessions[t]; };
  const needUser = () => who() || demoErr(401);
  const clean = v => { const n = String(v || "").split(/\s+/).filter(Boolean).join(" ").slice(0, 40); if (!n) demoErr(400, "Scrivi un nome"); return n; };
  let m;
  if (path === "/api/users" && method === "GET") return { users: db.users };
  if (path === "/api/users" && method === "POST"){
    const n = clean(body.name); if (db.users.includes(n)) demoErr(400, "Questo nome esiste già");
    if (db.users.length >= 10) demoErr(400, "Massimo 10 persone");
    db.users.push(n); save(); return { name: n };
  }
  if (path === "/api/users" && method === "PATCH"){
    const a = clean(body.from), b = clean(body.to);
    if (!db.users.includes(a)) demoErr(400, "Nome non trovato");
    if (a !== b && db.users.includes(b)) demoErr(400, "Questo nome esiste già");
    db.users = db.users.map(u => u === a ? b : u);
    for (const k in db.sessions) if (db.sessions[k] === a) db.sessions[k] = b;
    for (const k in db.items) if (db.items[k].updatedBy === a) db.items[k].updatedBy = b;
    save(); return { name: b };
  }
  if ((m = path.match(/^\/api\/users\/(.+)$/)) && method === "DELETE"){
    const n = decodeURIComponent(m[1]); db.users = db.users.filter(u => u !== n);
    for (const k in db.sessions) if (db.sessions[k] === n) delete db.sessions[k];
    save(); return { ok: true };
  }
  if (path === "/api/login"){
    const n = String(body.name || "").trim(); if (!db.users.includes(n)) demoErr(400, "Nome non riconosciuto");
    const t = newId(); db.sessions[t] = n; save(); ls.set(SESS_KEY, t); return { name: n };
  }
  if (path === "/api/logout"){ const t = ls.get(SESS_KEY, null); if (t) delete db.sessions[t]; save(); ls.set(SESS_KEY, null); return { ok: true }; }
  if (path === "/api/me"){ const u = who(); if (!u) demoErr(401); return { name: u }; }
  if (path === "/api/items" && method === "GET"){
    needUser();
    const sm = await demoSampleP;
    return { items: Object.entries(db.items).map(([id, v]) => ({ ...v, id })), spaces: db.spaces, ai: !!sm, now: Date.now() };
  }
  // lista della spesa (come shop_add e le API /api/shopping di server.py)
  db.shopping = db.shopping || {};
  const shopOut = (id, v) => ({ ...v, id });
  const shopAdd = (b, user) => {
    const name = String(b.name || "").split(/\s+/).filter(Boolean).join(" ").slice(0, 200); if (!name) demoErr(400, "Scrivi cosa comprare");
    const code = String(b.code || "").trim(), qty = Math.max(1, Math.min(99, parseInt(b.qty) || 1)), now = Date.now();
    const same = Object.entries(db.shopping).find(([, v]) => !v.done && (code ? v.code === code : v.name.toLowerCase() === name.toLowerCase()));
    if (same){
      const v = same[1]; v.qty = Math.min(99, v.qty + qty); v.updated = now;
      if (!v.cat) v.cat = String(b.cat || ""); if (!v.space) v.space = String(b.space || ""); if (b.item) v.item = String(b.item);
      return [shopOut(same[0], v), false];
    }
    const id = "p" + newId();
    db.shopping[id] = { name, cat: String(b.cat || ""), code, qty, done: false, space: String(b.space || ""), item: String(b.item || ""), created: now, updated: now, addedBy: user };
    return [shopOut(id, db.shopping[id]), true];
  };
  if (path === "/api/shopping" && method === "GET"){
    needUser();
    return { items: Object.entries(db.shopping).map(([id, v]) => shopOut(id, v)).sort((a, b) => (a.done - b.done) || (a.created - b.created)) };
  }
  if (path === "/api/shopping" && method === "POST"){ const u = needUser(); const [entry, created] = shopAdd(body, u); save(); return { entry, created }; }
  if (path === "/api/shopping" && method === "DELETE"){ needUser(); for (const k in db.shopping) if (db.shopping[k].done) delete db.shopping[k]; save(); return { ok: true }; }
  if ((m = path.match(/^\/api\/shopping\/(.+)$/))){
    needUser(); const id = decodeURIComponent(m[1]);
    if (method === "DELETE"){ delete db.shopping[id]; save(); return { ok: true }; }
    const v = db.shopping[id]; if (!v) demoErr(404, "Voce non trovata: forse l'ha già tolta qualcun altro");
    if ("qty" in body) v.qty = Math.max(1, Math.min(99, parseInt(body.qty) || 1));
    if ("done" in body) v.done = !!body.done;
    if ("name" in body){ const n = String(body.name || "").trim().slice(0, 200); if (!n) demoErr(400, "Scrivi cosa comprare"); v.name = n; }
    if ("cat" in body) v.cat = String(body.cat || "").slice(0, 100);
    v.updated = Date.now(); save(); return { entry: shopOut(id, v) };
  }
  if ((m = path.match(/^\/api\/items\/(.+)\/to-shopping$/))){
    const u = needUser(), id = decodeURIComponent(m[1]), it = db.items[id];
    if (!it) demoErr(404, "Prodotto non trovato: forse l'ha già tolto qualcun altro");
    const [entry, created] = shopAdd({ ...it, qty: 1, item: id }, u);
    let out = null;
    if ((parseInt(it.qty) || 0) > 1){ it.qty = parseInt(it.qty) - 1; it.updated = Date.now(); it.updatedBy = u; out = { ...it, id }; }
    else delete db.items[id];
    save(); return { entry, created, item: out };
  }
  if ((m = path.match(/^\/api\/items\/(.+)$/))){
    const u = needUser(), id = decodeURIComponent(m[1]);
    if (method === "DELETE"){ delete db.items[id]; save(); return { ok: true }; }
    const name = String(body.name || "").trim().slice(0, 200); if (!name) demoErr(400, "Il nome è obbligatorio");
    const v = { code: String(body.code || ""), name, space: String(body.space || ""), qty: Math.max(0, parseInt(body.qty) || 0),
      exp: String(body.exp || ""), cat: String(body.cat || ""), note: String(body.note || ""), updated: Date.now(), updatedBy: u };
    db.items[id] = v; save(); return { item: { ...v, id } };
  }
  if (path === "/api/recipes" && method === "POST"){
    needUser();
    const sm = await demoSampleP; if (!sm) demoErr(400, "Ricette non disponibili in questa anteprima");
    let prompt, n;
    if (Array.isArray(body.scelti)){           // scheda Ricette: prodotti spuntati
      const all = Object.entries(db.items).map(([id, v]) => ({ ...v, id })).filter(i => (+i.qty || 0) > 0);
      const ids = new Set(body.scelti.map(String)), chosen = all.filter(i => ids.has(i.id));
      if (!chosen.length) demoErr(400, "Spunta almeno un prodotto disponibile");
      const wish = String(body.richiesta || "").trim().slice(0, 300);
      prompt = `Sei un aiuto in cucina per una casa italiana. Proponi 3 ricette casalinghe per 2 persone costruite intorno ai prodotti che ho scelto.
Prodotti scelti (da usare):
${chosen.map(i => "- " + i.name).join("\n")}
Altri prodotti disponibili in casa (usali solo se servono):
${all.filter(i => !ids.has(i.id)).slice(0, 60).map(i => "- " + i.name).join("\n") || "- (nient'altro)"}
${wish ? "Le mie richieste: " + wish : ""}
Regole: ogni ricetta usa più prodotti scelti possibile (almeno uno); dai per scontati sale, pepe, olio, aceto e acqua; elenca solo gli ingredienti che mancano davvero. Passi brevi e pratici.
Rispondi SOLO con JSON valido, senza testo prima o dopo, in questa forma:
{"ricette":[{"titolo":"...","tempo":"25 min","perche":"una frase su come usa i prodotti scelti","usa":["nomi esatti dei prodotti di casa usati"],"mancano":["ingredienti da comprare"],"passi":["...","..."]}]}`;
      n = chosen.length;
    } else {
      [prompt, n] = demoPrompt(Object.values(db.items)); if (!n) demoErr(400, "Nessun prodotto in scadenza nei prossimi 7 giorni");
    }
    try {
      const data = await sm.json(prompt, { cache: !body.nuove });
      return { ricette: (data.ricette || []).filter(r => r && r.titolo).slice(0, 5).map(demoClean) };
    } catch (e) {
      demoErr(502, e && e.code === "not_granted" ? "Serve il tuo permesso per chiedere a Claude: riprova e consenti." :
                   e && e.code === "rate_limited" ? "Troppe richieste: riprova tra poco" : "Ricette non disponibili ora: riprova");
    }
  }
  if (path === "/api/ai/settings"){ needUser(); const sm = await demoSampleP; return { demo: true, engine: sm ? "api" : null, apiKeyTail: "", owner: true }; }
  if (path === "/api/update"){ needUser(); return { demo: true }; }
  if (path === "/api/push/settings"){
    const u = needUser(); db.push = db.push || {};
    if (method === "POST"){ db.push[u] = { days: Math.max(0, Math.min(30, +body.days || 0)), hour: Math.max(0, Math.min(23, +body.hour || 9)) }; save(); return db.push[u]; }
    return { days: 3, hour: 9, ...(db.push[u] || {}), devices: 0 };
  }
  if (path === "/api/read-date"){
    needUser();
    const sm = await demoSampleP; if (!sm) demoErr(400, "Lettura non disponibile in questa anteprima");
    const today = new Date().toISOString().slice(0, 10);
    const prompt = `Nella foto c'è la data di scadenza di un prodotto alimentare (vicino a scritte come "da consumarsi entro", "da consumarsi preferibilmente entro", "TMC", "scad.", "exp", "best before"). Oggi è ${today}.
Regole: formato europeo giorno/mese/anno; anno a due cifre = 20xx; se ci sono solo mese e anno usa l'ultimo giorno di quel mese; se ci sono più date (produzione, lotto, scadenza) scegli quella di scadenza.
Rispondi SOLO con JSON valido: {"data":"AAAA-MM-GG oppure null","letto":"il testo della data così come è stampato"}`;
    try {
      const r = await sm.json(prompt, { images: [blob], cache: false });
      const d = r && typeof r.data === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.data) && !isNaN(Date.parse(r.data)) ? r.data : null;
      return { data: d, letto: String((r && r.letto) || "").slice(0, 80) };
    } catch (e) {
      demoErr(502, e && e.code === "not_granted" ? "Serve il tuo permesso per chiedere a Claude: riprova e consenti." :
                   e && e.code === "images_unavailable" ? "Foto non supportate in questa anteprima" : "Lettura non riuscita: riprova");
    }
  }
  if ((m = path.match(/^\/api\/product\/(\d{6,14})$/))){
    needUser();
    // nella prova il browser chiede direttamente a Open Food Facts (consente le richieste da altri siti)
    try {
      const r = await fetch("https://world.openfoodfacts.org/api/v2/product/" + m[1] + ".json?fields=product_name,product_name_it,brands,quantity,categories_tags");
      const j = await r.json(), p = j.product || {};
      const name = (p.product_name_it || p.product_name || "").trim();
      if (j.status !== 1 || !name) return { found: false };
      const brand = (p.brands || "").split(",")[0].trim(), qty = (p.quantity || "").trim();
      let full = name;
      if (brand && !name.toLowerCase().includes(brand.toLowerCase())) full += " " + brand;
      if (qty && !full.toLowerCase().includes(qty.toLowerCase())) full += " " + qty;
      const map = [["en:frozen-foods","Surgelati"],["en:pastas","Pasta"],["en:canned-foods","Conserve"],["en:sauces","Sughi"],
        ["en:dairies","Latticini"],["en:meats","Carne"],["en:fishes","Pesce"],["en:biscuits","Biscotti"],["en:beverages","Bevande"],["en:snacks","Snack"]];
      const tags = new Set(p.categories_tags || []), hit = map.find(([t]) => tags.has(t));
      return { found: true, name: full, brand, quantity: qty, cat: hit ? hit[1] : "" };
    } catch { demoErr(502, "Open Food Facts non raggiungibile"); }
  }
  if (path === "/api/recognize"){
    needUser();
    const sm = await demoSampleP; if (!sm) demoErr(400, "Riconoscimento non disponibile in questa anteprima");
    const today = new Date().toISOString().slice(0, 10);
    const cats = [...new Set(Object.values(db.items).map(i => (i.cat || "").trim()).filter(Boolean))].sort().slice(0, 40);
    const prompt = `Nella foto c'è un prodotto da dispensa, frigo o casa (confezione, frutta, verdura, prodotto del banco…). Oggi è ${today}.
Riconoscilo e rispondi SOLO con JSON valido:
{"nome":"nome breve con marca e formato se si leggono, es. «Pelati Mutti 400 g» o «Mele Golden»",
"categoria":"una categoria breve in italiano",
"pezzi":numero di confezioni o pezzi uguali visibili (1 se è uno solo),
"scadenza":"AAAA-MM-GG se una data di scadenza è leggibile, altrimenti null",
"codice":"le cifre del codice a barre se sono leggibili per intero, altrimenti null"}
Per la categoria usa, se adatta, una di queste che sono già in uso: ${cats.join(", ") || "nessuna, scegline tu una"}.
Per la scadenza: formato europeo giorno/mese/anno, anno a due cifre = 20xx, solo mese e anno = ultimo giorno del mese.
Se nella foto non c'è un prodotto riconoscibile, metti "nome": null.`;
    try {
      const r = await sm.json(prompt, { images: [blob], cache: false }) || {};
      const exp = typeof r.scadenza === "string" && /^\d{4}-\d{2}-\d{2}$/.test(r.scadenza) && !isNaN(Date.parse(r.scadenza)) ? r.scadenza : null;
      const code = String(r.codice || "").replace(/\D/g, "");
      return { nome: String(r.nome || "").trim().slice(0, 200) || null, categoria: String(r.categoria || "").trim().slice(0, 60),
               pezzi: Math.max(1, Math.min(99, parseInt(r.pezzi) || 1)), scadenza: exp, codice: code.length >= 8 && code.length <= 14 ? code : "" };
    } catch (e) {
      demoErr(502, e && e.code === "not_granted" ? "Serve il tuo permesso per chiedere a Claude: riprova e consenti." :
                   e && e.code === "images_unavailable" ? "Foto non supportate in questa anteprima" : "Riconoscimento non riuscito: riprova");
    }
  }
  if (path === "/api/saved-recipes" && method === "GET"){
    needUser(); db.recipes = db.recipes || {};
    return { recipes: Object.entries(db.recipes).sort((a, b) => b[1].created - a[1].created).map(([id, v]) => ({ ...v, id })) };
  }
  if ((m = path.match(/^\/api\/saved-recipes\/(.+)$/))){
    const u = needUser(), id = decodeURIComponent(m[1]); db.recipes = db.recipes || {};
    if (method === "DELETE"){ delete db.recipes[id]; save(); return { ok: true }; }
    const r = demoClean(body); if (!r.titolo) demoErr(400, "Ricetta senza titolo");
    db.recipes[id] = { ...r, savedBy: u, created: Date.now() }; save(); return { ...r, id, savedBy: u };
  }
  if (path === "/api/spaces" && method === "POST"){
    needUser(); const n = clean(body.name);
    if (db.spaces.some(s => s.name === n)) demoErr(400, "Questo ambiente esiste già");
    if (db.spaces.length >= 12) demoErr(400, "Massimo 12 ambienti");
    const s = { id: "s" + newId(), name: n }; db.spaces.push(s); save(); return s;
  }
  if (path === "/api/spaces" && method === "PATCH"){
    needUser(); const n = clean(body.name);
    if (db.spaces.some(s => s.name === n && s.id !== body.id)) demoErr(400, "Questo ambiente esiste già");
    const s = db.spaces.find(s => s.id === body.id); if (s) s.name = n; save(); return { id: body.id, name: n };
  }
  if ((m = path.match(/^\/api\/spaces\/(.+)$/)) && method === "DELETE"){
    needUser(); const sid = decodeURIComponent(m[1]);
    if (db.spaces.length <= 1) demoErr(400, "Serve almeno un ambiente");
    const used = Object.values(db.items).filter(v => v.space === sid).length;
    if (used) demoErr(400, used === 1 ? "Contiene ancora 1 prodotto: spostalo o eliminalo prima" : `Contiene ancora ${used} prodotti: spostali o eliminali prima`);
    db.spaces = db.spaces.filter(s => s.id !== sid); save(); return { ok: true };
  }
  demoErr(404, "Non trovato");
}
let demoArmed = false;
$("demoReset").onclick = () => {
  if (!demoArmed){ demoArmed = true; $("demoReset").textContent = "Tocca di nuovo"; return; }
  try { [DEMO_KEY, SESS_KEY, CACHE_KEY, QUEUE_KEY, ME_KEY, SPACES_KEY, CUR_KEY].forEach(k => localStorage.removeItem(k)); } catch {}
  location.reload();
};
'''

# sostituisce la vera api() (fetch al NAS) con quella simulata
start = js.index("async function api(method, path, body){")
end = js.index("function saveLocal()")
js = js[:start] + js[end:]
js = rep(js, "/* ---------------- chi sei ---------------- */", MOCK + "\n/* ---------------- chi sei ---------------- */")
# niente fotocamera dal vivo nell'anteprima: Scansiona apre subito la foto
js = rep(js, "if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){", "if (true){")
js = rep(js, 'if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(()=>{});', "")
js = js.replace("NAS non raggiungibile", "Prova non disponibile")
js = rep(js, "// PUSH_SUPPORT_CHECK", 'return "Nella prova le notifiche non arrivano: scegli qui anticipo e ora, poi le attivi dall\'app installata dal NAS.";')
# backup: nell'anteprima il file si salva tramite la capability "downloads"
js = rep(js, """  if (navigator.canShare && navigator.canShare({ files: [file] })){""", """  try {
    const dl = window.claude && window.claude.use ? await window.claude.use("downloads") : null;
    if (dl){ const r = await dl.save({ filename: file.name, data: json }); if (r) { done(); toast("Backup salvato"); } return; }
  } catch (e) { toast("Backup non salvato"); return; }
  if (navigator.canShare && navigator.canShare({ files: [file] })){""")

page = f"""<title>Dispensa di Casa</title>
<style>{style}</style>
{body.replace("__APP__", js)}"""
open(OUT, "w").write(page)
print("ok", len(page))
