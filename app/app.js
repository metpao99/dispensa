(() => {
"use strict";
/* Versione dell'app: si aumenta a ogni push su main (vedi CLAUDE.md).
   Piccole modifiche e correzioni: +0.1 (0.1 → 0.2). Miglioramenti grandi: unità (0.9 → 1.0). */
const APP_VERSION = "0.4";
const $ = id => document.getElementById(id);
$("appVer").textContent = "Dispensa " + APP_VERSION;
const SPACES_KEY = "dispensa-spaces", CUR_KEY = "dispensa-current-space";
const CACHE_KEY = "dispensa-cache-v2", QUEUE_KEY = "dispensa-queue-v2", ME_KEY = "dispensa-me", BK = "dispensa-last-backup";
const ls = {
  get(k, d){ try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }
};
let items = ls.get(CACHE_KEY, []);
let queue = ls.get(QUEUE_KEY, []);      // modifiche non ancora arrivate al NAS
let me = ls.get(ME_KEY, null);
let spaces = ls.get(SPACES_KEY, [{ id: "dispensa", name: "Dispensa" }, { id: "frigo", name: "Frigo" }]);
let current = ls.get(CUR_KEY, null);
let aiOn = ls.get("dispensa-ai", false);
let online = true, flushing = false;
let filter = "Tutti", query = "", editing = null;

/* ---------------- niente zoom: deve sembrare un'app ---------------- */
for (const ev of ["gesturestart", "gesturechange", "gestureend"])
  document.addEventListener(ev, e => e.preventDefault(), { passive: false });
document.addEventListener("touchmove", e => { if (e.touches.length > 1 && e.cancelable) e.preventDefault(); }, { passive: false });
// il doppio tocco per ingrandire lo ferma il CSS (touch-action su html)

/* Safari (web app sulla Home), al ritorno dalla fotocamera, a volte non ridisegna lo schermo:
   aspettiamo che la pagina sia di nuovo visibile e forziamo un ridisegno. */
function whenVisible(){
  return new Promise(ok => {
    const go = () => requestAnimationFrame(() => requestAnimationFrame(ok));
    if (document.visibilityState === "visible") return go();
    const h = () => { if (document.visibilityState === "visible"){ document.removeEventListener("visibilitychange", h); go(); } };
    document.addEventListener("visibilitychange", h);
  });
}
function repaint(){
  const y = window.scrollY, b = document.body;
  b.style.display = "none"; void b.offsetHeight; b.style.display = "";
  window.scrollTo(0, y);
}

/* ---------------- pannelli (stile iOS) ----------------
   openSheet/closeSheet animano l'entrata e l'uscita dal basso. Ogni pannello si può
   chiudere tirandolo giù: dalla maniglia sempre, dal contenuto solo quando è già in cima. */
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const SHEET_MS = 420;
const sheetClosers = {};           // chiusure con effetti extra (es. la scheda prodotto azzera "editing")
function openSheet(id, instant){
  const s = $(id), p = s.querySelector(".panel");
  clearTimeout(s._t);
  const wasHidden = s.hidden || s.classList.contains("closing");
  s.classList.remove("closing", "dragging");
  if (!wasHidden) return;
  if (s.hidden && instant){                // senza animazione (es. al ritorno dalla fotocamera)
    s.hidden = false; p.style.transform = ""; s.style.removeProperty("--dim"); return;
  }
  if (s.hidden){
    s.hidden = false;
    s.classList.add("dragging");            // posizione di partenza senza transizione
    p.style.transform = "translateY(100%)"; s.style.setProperty("--dim", 0);
    void p.offsetHeight;
    s.classList.remove("dragging");
  }
  p.style.transform = ""; s.style.removeProperty("--dim");
}
function closeSheet(id){
  const s = $(id), p = s.querySelector(".panel");
  if (s.hidden || s.classList.contains("closing")) return;
  if (s.contains(document.activeElement)) document.activeElement.blur();
  const done = () => { s.hidden = true; s.classList.remove("closing", "dragging"); p.style.transform = ""; s.style.removeProperty("--dim"); };
  if (reduceMotion.matches) return done();
  s.classList.remove("dragging"); s.classList.add("closing");
  p.style.transform = "translateY(100%)"; s.style.setProperty("--dim", 0);
  s._t = setTimeout(done, SHEET_MS);
}
function dismissSheet(id){ (sheetClosers[id] || closeSheet)(id); }
function scrolledInside(t, panel){
  for (let n = t; n && n !== panel; n = n.parentElement)
    if (n.scrollTop > 0 && n.scrollHeight > n.clientHeight) return true;
  return panel.scrollTop > 0;
}
function setupSheet(s){
  const p = s.querySelector(".panel");
  const grab = document.createElement("div"); grab.className = "grab"; grab.setAttribute("aria-hidden", "true");
  p.prepend(grab);
  let g = null;   // gesto in corso
  p.addEventListener("touchstart", e => {
    if (e.touches.length !== 1 || s.classList.contains("closing")) { g = null; return; }
    const t = e.touches[0], fromGrab = !!e.target.closest(".grab");
    // da pulsanti e campi non si trascina: il tocco deve restare un tocco (es. "Salva" con la tastiera aperta)
    if (!fromGrab && e.target.closest("button, input, select, textarea, label, a, [role=button]")) { g = null; return; }
    g = { x: t.clientX, y: t.clientY, top: fromGrab || !scrolledInside(e.target, p), on: false, d: 0, pts: [] };
  }, { passive: true });
  p.addEventListener("touchmove", e => {
    if (!g) return;
    const t = e.touches[0], dx = t.clientX - g.x, dy = t.clientY - g.y;
    if (!g.on){
      // va trascinato solo se si tira verso il basso, in verticale, partendo dalla cima
      if (!g.top || dy < 0 || Math.abs(dx) > Math.max(8, dy)) { g = null; return; }
      if (e.cancelable) e.preventDefault();      // niente rimbalzo di Safari mentre si decide
      if (dy < 8) return;                        // sotto gli 8 px è ancora un tocco
      g.on = true; g.y = t.clientY; s.classList.add("dragging");
      if (p.contains(document.activeElement)) document.activeElement.blur();
    }
    if (e.cancelable) e.preventDefault();
    const raw = t.clientY - g.y;
    g.d = raw >= 0 ? raw : -12 * Math.log1p(-raw / 12);   // verso l'alto: resistenza elastica
    p.style.transform = `translateY(${g.d}px)`;
    s.style.setProperty("--dim", Math.max(0, 1 - Math.max(0, g.d) / p.offsetHeight));
    const now = performance.now();
    g.pts.push([now, g.d]); while (g.pts.length > 2 && now - g.pts[0][0] > 100) g.pts.shift();
  }, { passive: false });
  const end = cancel => {
    if (!g || !g.on) { g = null; return; }
    const [t0, d0] = g.pts[0] || [0, 0], dt = performance.now() - t0;
    const v = dt > 0 ? (g.d - d0) / dt : 0;    // px/ms, positivo verso il basso
    const far = g.d > Math.min(p.offsetHeight * 0.33, 180), fast = v > 0.45 && g.d > 12;
    g = null; s.classList.remove("dragging");
    if (!cancel && (far || fast)) dismissSheet(s.id);
    else { p.style.transform = ""; s.style.removeProperty("--dim"); }
  };
  p.addEventListener("touchend", () => end(false));
  p.addEventListener("touchcancel", () => end(true));
}
document.querySelectorAll(".sheet").forEach(setupSheet);
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  const open = [...document.querySelectorAll(".sheet:not([hidden]):not(.closing)")].pop();
  if (open) dismissSheet(open.id);
});

/* ---------------- barra in basso: schede Dispensa / Menu ---------------- */
const tabScroll = { home: 0, recipes: 0, menu: 0 };
let tab = "home";
function showTab(name){
  if (name === tab) return;
  tabScroll[tab] = window.scrollY;
  tab = name;
  const views = { home: $("homeView"), recipes: $("recipesView"), menu: $("menuView") };
  for (const k in views){ views[k].hidden = k !== name; views[k].classList.toggle("enter", k === name); }
  $("homeTab").setAttribute("aria-selected", name === "home");
  $("recipesTab").setAttribute("aria-selected", name === "recipes");
  $("menuBtn").setAttribute("aria-selected", name === "menu");
  if (name === "recipes") renderPick();
  document.querySelector(".tabs-pill").dataset.tab = name;
  $("scanBtn").classList.toggle("off", name !== "home");
  window.scrollTo(0, tabScroll[name]);
}
const toTop = () => window.scrollTo({ top: 0, behavior: "smooth" });   // ritocco della scheda già aperta: torna su
$("homeTab").onclick = () => tab === "home" ? toTop() : showTab("home");
$("recipesTab").onclick = () => tab === "recipes" ? toTop() : showTab("recipes");
/* La "lente" della barra si trascina col dito, come in iOS: tieni premuto, scorri,
   lasci sopra una scheda e si apre quella. Un tocco semplice resta un tocco. */
{
  const pill = document.querySelector(".tabs-pill"), lens = pill.querySelector(".lens");
  const btns = [$("homeTab"), $("recipesTab"), $("menuBtn")], names = ["home", "recipes", "menu"];
  let g = null, skipClick = false;
  const geo = () => { const r = pill.getBoundingClientRect(), w = (r.width - 8) / btns.length; return { r, w }; };
  const indexAt = x => { const { r, w } = geo(); return Math.max(0, Math.min(btns.length - 1, Math.floor((x - r.left - 4) / w))); };
  pill.addEventListener("pointerdown", e => {
    if (e.button > 0) return;
    pill.classList.add("press");
    g = { x: e.clientX, id: e.pointerId, moved: false };
  });
  pill.addEventListener("pointermove", e => {
    if (!g || e.pointerId !== g.id) return;
    if (!g.moved){
      if (Math.abs(e.clientX - g.x) < 6) return;
      g.moved = true; pill.classList.add("sliding");
      try { pill.setPointerCapture(e.pointerId); } catch {}
    }
    const { r, w } = geo();
    const x = Math.max(0, Math.min(r.width - 8 - w, e.clientX - r.left - 4 - w / 2));
    lens.style.transform = `translateX(${x}px) scale(1.12, 1.06)`;     // la lente segue il dito e si allarga un po'
    const i = indexAt(e.clientX);
    btns.forEach((b, k) => b.classList.toggle("hover", k === i));
  });
  const end = e => {
    if (!g || (e && e.pointerId !== g.id)) return;
    const moved = g.moved; g = null;
    pill.classList.remove("press", "sliding");
    btns.forEach(b => b.classList.remove("hover"));
    lens.style.transform = "";                                      // la molla la porta sulla scheda giusta
    if (moved && e && e.type === "pointerup"){
      skipClick = true; setTimeout(() => skipClick = false, 400);
      const i = indexAt(e.clientX);
      if (names[i] !== tab) btns[i].onclick();
    }
  };
  pill.addEventListener("pointerup", end);
  pill.addEventListener("pointercancel", end);
  pill.addEventListener("lostpointercapture", e => { if (g && g.moved) end(e); });
  pill.addEventListener("pointerleave", e => { if (g && !g.moved) end(); });   // dito uscito senza trascinare
  // dopo un trascinamento il "click" finale non deve aprire di nuovo la scheda
  pill.addEventListener("click", e => { if (skipClick){ e.stopPropagation(); e.preventDefault(); } }, true);
}

/* ---------------- dati: NAS + copia locale ---------------- */
class AuthError extends Error {}
async function api(method, path, body){
  const r = await fetch(path, {
    method, credentials: "same-origin", cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined
  });
  if (r.status === 401) throw new AuthError();
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.error || "Errore " + r.status); e.status = r.status; throw e; }
  return data;
}
function saveLocal(){ ls.set(CACHE_KEY, items); ls.set(QUEUE_KEY, queue); ls.set(SPACES_KEY, spaces); }
/* ambiente di un prodotto: id valido, oppure (vecchi dati) nome del posto, oppure il primo ambiente */
function spaceOf(it){
  if (spaces.some(s => s.id === it.space)) return it.space;
  const byName = spaces.find(s => s.name.toLowerCase() === String(it.place || it.space || "").toLowerCase());
  return byName ? byName.id : (spaces[0] && spaces[0].id);
}
function curSpace(){ return spaces.find(s => s.id === current) || spaces[0] || { id: "", name: "Dispensa" }; }
function setCurrent(id){ current = id; ls.set(CUR_KEY, id); filter = "Tutti"; render(); }
function applyLocal(op){
  if (op.op === "put"){ const i = items.findIndex(x => x.id === op.id); i >= 0 ? items[i] = op.item : items.push(op.item); }
  else items = items.filter(x => x.id !== op.id);
}
function upsert(it){
  it = { ...it, updatedBy: me };
  const op = { op: "put", id: it.id, item: it };
  queue = queue.filter(q => q.id !== it.id); queue.push(op);
  applyLocal(op); saveLocal(); render(); flush();
}
function remove(id){
  const op = { op: "del", id };
  queue = queue.filter(q => q.id !== id); queue.push(op);
  applyLocal(op); saveLocal(); render(); flush();
}
async function flush(){
  if (flushing) return; flushing = true;
  try {
    while (queue.length){
      const op = queue[0];
      try {
        if (op.op === "put"){
          const { id, updated, updatedBy, ...body } = op.item;
          const r = await api("PUT", "/api/items/" + encodeURIComponent(op.id), body);
          const i = items.findIndex(x => x.id === op.id); if (i >= 0 && queue.filter(q => q.id === op.id).length === 1) items[i] = r.item;
        } else {
          await api("DELETE", "/api/items/" + encodeURIComponent(op.id));
        }
        queue.shift(); setOnline(true);
      } catch (e) {
        if (e instanceof AuthError){ showLogin(); break; }
        if (e.status >= 400 && e.status < 500){ queue.shift(); toast("Modifica scartata: " + e.message); continue; }
        setOnline(false); break;                 // rete assente: riprovo dopo
      }
      saveLocal();
    }
  } finally { flushing = false; saveLocal(); render(); }
}
async function refresh(){
  try {
    await flush();
    if (queue.length) return;                    // non sovrascrivere modifiche non ancora inviate
    const r = await api("GET", "/api/items");
    items = r.items; if (r.spaces && r.spaces.length) spaces = r.spaces;
    aiOn = !!r.ai; ls.set("dispensa-ai", aiOn);
    saveLocal(); setOnline(true); render();
  } catch (e) {
    if (e instanceof AuthError) showLogin(); else setOnline(false);
  }
}
function setOnline(v){
  online = v;
  const s = $("sync");
  if (v && !queue.length){ s.hidden = true; return; }
  s.hidden = false;
  s.className = "sync" + (v ? "" : " off");
  s.textContent = v ? "Sincronizzazione…" : `NAS non raggiungibile: lavoro offline${queue.length ? `, ${queue.length} modifiche da inviare` : ""}.`;
}

/* ---------------- chi sei ---------------- */
async function showLogin(){
  $("login").hidden = false;
  const list = $("userList"), msg = $("loginMsg");
  list.textContent = ""; msg.className = "status"; msg.textContent = "Carico i nomi…";
  try {
    const r = await api("GET", "/api/users");
    msg.textContent = r.users.length ? "" : "Nessuna persona ancora: scrivi il tuo nome e tocca Aggiungi.";
    for (const name of r.users){
      const b = el("button", "btn", name); b.type = "button";
      b.onclick = async () => {
        try { const res = await api("POST", "/api/login", { name }); setMe(res.name); $("login").hidden = true; refresh(); }
        catch { msg.className = "status err"; msg.textContent = "Accesso non riuscito: controlla la connessione al NAS."; }
      };
      list.append(b);
    }
  } catch {
    msg.className = "status err";
    msg.textContent = "NAS non raggiungibile. Controlla di essere sul Wi-Fi di casa o con Tailscale attivo, poi riapri l'app.";
  }
}
$("addPersonForm").onsubmit = async e => {
  e.preventDefault();
  const name = $("newPerson").value.trim(), msg = $("loginMsg"); if (!name) return;
  try { await api("POST", "/api/users", { name }); $("newPerson").value = ""; showLogin(); }
  catch (err) { msg.className = "status err"; msg.textContent = err.message || "Non aggiunto: controlla la connessione al NAS."; }
};

/* ---------------- scadenza dalla foto ---------------- */
$("readDate").onclick = () => { $("expPhoto").value = ""; $("expPhoto").click(); };
async function photoToJpeg(file, max){
  const url = URL.createObjectURL(file), img = new Image(); img.src = url;
  try { await img.decode(); } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  const s = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement("canvas"); c.width = Math.round(img.naturalWidth * s); c.height = Math.round(img.naturalHeight * s);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return new Promise(r => c.toBlob(r, "image/jpeg", 0.85));
}
function blobToB64(b){ return new Promise((ok, ko) => { const f = new FileReader(); f.onload = () => ok(String(f.result).split(",")[1]); f.onerror = ko; f.readAsDataURL(b); }); }
$("expPhoto").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  const msg = $("readMsg"), btn = $("readDate");
  msg.hidden = false; msg.className = "readmsg full"; msg.textContent = "Leggo la data…"; btn.disabled = true;
  try {
    const jpg = await photoToJpeg(f, 1280);
    const r = await api("POST", "/api/read-date", { image: await blobToB64(jpg) }, jpg);
    if (r.data){
      $("f-exp").value = r.data;
      const nice = new Date(r.data + "T00:00:00").toLocaleDateString("it-IT", { day: "numeric", month: "long", year: "numeric" });
      msg.textContent = `Letto “${r.letto || r.data}” → ${nice}. Controlla che sia giusta.`;
    } else { msg.className = "readmsg full err"; msg.textContent = "Non trovo la data: avvicinati alla scritta, con buona luce."; }
  } catch (err) {
    msg.className = "readmsg full err"; msg.textContent = err instanceof AuthError ? "Scegli prima chi sei." : (err.message || "Lettura non riuscita.");
  } finally { btn.disabled = false; }
};

/* ---------------- riconosci il prodotto dalla foto (AI) ----------------
   Dal pulsante rotondo si apre PRIMA la fotocamera e la scheda solo a foto pronta:
   aprire la scheda (con animazione) e subito dopo la fotocamera su iOS può lasciare
   la scheda non ridisegnata al ritorno. */
let recFromFab = false, recRun = 0;
$("recBtn").onclick = () => { recFromFab = false; $("recPhoto").value = ""; $("recPhoto").click(); };
$("recPhoto").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  const open = recFromFab || !formOpenNew();
  recFromFab = false;
  await whenVisible();                       // di nuovo in primo piano dopo la fotocamera
  if (open) openForm(null, "", null, "", true, true);
  repaint();
  recognizeFile(f);
};
function withTimeout(p, ms, text){
  return Promise.race([p, new Promise((_, ko) => setTimeout(() => ko(new Error(text)), ms))]);
}
async function recognizeFile(f){
  const run = ++recRun, msg = $("recMsg"), btn = $("recBtn"), t0 = Date.now();
  const say = (cls, text) => { msg.hidden = false; msg.className = "readmsg" + (cls ? " " + cls : ""); msg.textContent = text; };
  say("thinking", "Preparo la foto…"); btn.disabled = true;
  const tick = setInterval(() => { if (run === recRun && msg.classList.contains("thinking"))
    msg.textContent = `Guardo la foto… ${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
  try {
    const jpg = await withTimeout(photoToJpeg(f, 1280), 20000, "Non riesco a preparare la foto: riprova.");
    say("thinking", "Guardo la foto…");
    const r = await withTimeout(api("POST", "/api/recognize", { image: await blobToB64(jpg) }, jpg), 120000,
                                "Il NAS non ha risposto in tempo: riprova.");
    if (run !== recRun) return;                         // nel frattempo è partita un'altra foto
    if (!formOpenNew()) openForm(null, "", null, "", true, true);   // la scheda è stata chiusa: la riapro
    const known = byCode(r.codice || $("f-code").value);
    if (known){ openRestock(known, r.scadenza || $("f-exp").value, true); return; }
    if (!r.nome){ say("err", "Non riconosco il prodotto: inquadra bene la confezione, con buona luce."); return; }
    const filled = [];
    const put = (id, v, label) => { if (v && !$(id).value.trim()){ $(id).value = v; filled.push(label); } };
    put("f-name", r.nome, "nome");
    put("f-cat", r.categoria && sameCat(r.categoria), "categoria");
    put("f-exp", r.scadenza, "scadenza");
    put("f-code", r.codice, "codice");
    if (r.pezzi > 1 && $("f-qty").value === "1"){ $("f-qty").value = r.pezzi; filled.push("quantità"); }
    if (document.activeElement && $("sheet").contains(document.activeElement)) document.activeElement.blur();
    say("", `Riconosciuto: “${r.nome}”. ` +
      (filled.length ? `Ho compilato ${filled.join(", ")}: controlla che sia tutto giusto.` : "I campi erano già compilati, non ho cambiato nulla."));
    $("sheet").querySelector(".panel").scrollTop = 0;
  } catch (err) {
    if (run !== recRun) return;
    say("err", err instanceof AuthError ? "Scegli prima chi sei." : (err.message || "Riconoscimento non riuscito."));
  } finally { clearInterval(tick); if (run === recRun){ btn.disabled = false; repaint(); } }
}

/* ---------------- intelligenza artificiale (impostazioni) ---------------- */
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("it-IT", { day: "numeric", month: "long", year: "numeric" });
async function renderAI(){
  const box = $("aiBox"); box.textContent = "";
  let s;
  try { s = await api("GET", "/api/ai/settings"); }
  catch { box.append(el("p", "status", "Impostazioni non disponibili: NAS non raggiungibile.")); return; }
  const state = el("p", "aistate" + (s.engine ? " on" : ""));
  state.textContent = s.engine === "api" ? (s.demo ? "Attiva (versione di prova)" : `Attiva con chiave API (…${s.apiKeyTail})`)
    : s.engine === "claude" ? `Attiva con il tuo abbonamento Claude${s.tokenExpires ? " · da rinnovare entro il " + fmtDate(s.tokenExpires) : ""}`
    : "Non attiva: le ricette si chiedono con “Chiedi a…”.";
  box.append(state);
  if (s.demo){ box.append(el("p", "status", "Nella prova l'AI usa direttamente il tuo account Claude. Sul NAS, qui colleghi il tuo abbonamento (Claude Code) oppure una chiave API.")); return; }
  if (!s.owner){ box.append(el("p", "status", `Queste impostazioni le gestisce ${s.ownerName || "la proprietaria dell'app"}.`)); return; }

  const msg = el("p", "status");
  const say = (t, bad) => { msg.className = "status" + (bad ? " err" : ""); msg.textContent = t; };
  const call = async (path, body, ok) => {
    try { await api("POST", path, body); toast(ok); renderAI(); refresh(); }
    catch (e) { say(e.message || "Operazione non riuscita", true); }
  };

  // Claude Code con l'abbonamento
  box.append(el("p", null, "Abbonamento Claude (solo per te), tramite Claude Code sul NAS:"));
  if (!s.claudeInstalled){
    box.append(el("p", "status", "Claude Code non è installato sul NAS. Una sola volta, via SSH:"),
      el("code", "cmd", "curl -fsSL https://claude.ai/install.sh | bash"),
      el("p", "status", "Poi riavvia il server con sh start.sh e riapri questo menu."));
  } else if (!s.claudeToken){
    box.append(el("p", "status", "Genera il token con “claude setup-token” (sul NAS via SSH, oppure su un PC con Claude Code): apri il link, accetta e copia il codice che inizia con sk-ant-. Vale un anno."));
    const row = el("div", "aibox-row");
    const inp = el("input"); inp.type = "password"; inp.placeholder = "sk-ant-…"; inp.autocomplete = "off"; inp.setAttribute("aria-label", "Token di Claude");
    const b = el("button", "btn primary", "Collega"); b.type = "button";
    b.onclick = () => call("/api/ai/claude-token", { token: inp.value.trim() }, "Claude collegato");
    row.append(inp, b); box.append(row);
  } else {
    box.append(el("p", "status", `Collegato il ${fmtDate(s.tokenSaved)}. Il token resta salvato solo sul NAS.`));
    const row = el("div", "actions");
    const renew = el("button", "btn", "Rinnova token"); renew.type = "button";
    renew.onclick = () => call("/api/ai/forget", { what: "claude" }, "Incolla il nuovo token");
    const off = el("button", "btn danger", "Scollega"); off.type = "button"; let armed = false;
    off.onclick = () => { if (!armed){ armed = true; off.textContent = "Conferma"; return; } call("/api/ai/forget", { what: "claude" }, "Claude scollegato"); };
    row.append(renew, off); box.append(row);
  }

  // Chiave API (alternativa)
  const det = el("details", "more"); det.append(el("summary", null, s.apiKey ? "Chiave API (in uso)" : "Oppure usa una chiave API"));
  det.append(el("p", "status", "Con una chiave API (console.anthropic.com, a consumo) l'AI funziona per tutte le persone dell'app e ha la precedenza sull'abbonamento."));
  if (s.apiKey){
    const off = el("button", "btn danger", `Rimuovi chiave …${s.apiKeyTail}`); off.type = "button";
    off.onclick = () => call("/api/ai/forget", { what: "api" }, "Chiave rimossa");
    det.append(off);
  } else {
    const row = el("div", "aibox-row");
    const inp = el("input"); inp.type = "password"; inp.placeholder = "sk-ant-api…"; inp.autocomplete = "off"; inp.setAttribute("aria-label", "Chiave API");
    const b = el("button", "btn", "Salva"); b.type = "button";
    b.onclick = () => call("/api/ai/api-key", { key: inp.value.trim() }, "Chiave salvata");
    row.append(inp, b); det.append(row);
  }
  box.append(det);

  if (s.engine){
    const t = el("button", "btn", "Prova collegamento"); t.type = "button";
    t.onclick = async () => {
      t.disabled = true; say("Provo… (con Claude Code può volerci qualche secondo)");
      try { const r = await api("POST", "/api/ai/test", {}); say(`Funziona (${r.engine === "api" ? "chiave API" : "abbonamento Claude"}): “${r.reply}”`); }
      catch (e) { say(e.message || "Prova non riuscita", true); }
      finally { t.disabled = false; }
    };
    box.append(t);
  }
  box.append(msg);
}
async function checkAIExpiry(){
  try {
    const s = await api("GET", "/api/ai/settings");
    const left = s.owner && s.claudeToken && s.tokenExpires ? Math.round((new Date(s.tokenExpires + "T00:00:00") - Date.now()) / DAY) : null;
    $("aiBanner").hidden = !(left !== null && left <= 30);
    if (left !== null && left <= 30) $("aiBannerText").textContent = left > 0 ? `Il collegamento a Claude scade tra ${left} giorni: rinnovalo.` : "Il collegamento a Claude è scaduto: rinnovalo.";
  } catch {}
}
$("aiBannerBtn").onclick = () => $("menuBtn").click();

/* ---------------- aggiornamenti (da GitHub) ---------------- */
const fmtWhen = v => new Date(v).toLocaleString("it-IT", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
async function renderUpdate(){
  const box = $("updBox"); box.textContent = "";
  let s;
  try { s = await api("GET", "/api/update"); }
  catch { box.append(el("p", "status", "Stato non disponibile: NAS non raggiungibile.")); return; }
  if (s.demo){ box.append(el("p", "status", "Nella prova non servono. Sul NAS, qui installi le nuove versioni pubblicate su GitHub.")); return; }
  box.append(el("p", "aistate" + (s.available ? "" : " on"),
    s.running ? "Aggiornamento in corso…" : s.available ? "È disponibile una nuova versione" : s.latest ? "L'app è aggiornata" : "Versione su GitHub non ancora controllata"));
  box.append(el("p", "status", `Installata: ${s.installed.label}${s.installed.message ? " · " + s.installed.message : ""}`));
  if (s.available) box.append(el("p", "status", `Nuova: ${s.latest.sha.slice(0, 7)} · ${s.latest.message} (${fmtWhen(s.latest.date)})`));
  if (s.error) box.append(el("p", "status err", s.error));
  if (s.failed) box.append(el("p", "status err", "L'ultimo aggiornamento non è riuscito: " + s.failed));
  if (!s.owner){ box.append(el("p", "status", `Gli aggiornamenti li installa ${s.ownerName || "la proprietaria dell'app"}.`)); return; }

  const msg = el("p", "status");
  const say = (t, bad) => { msg.className = "status" + (bad ? " err" : ""); msg.textContent = t; };
  const row = el("div", "actions");
  const chk = el("button", "btn", "Controlla ora"); chk.type = "button";
  chk.onclick = async () => {
    chk.disabled = true; say("Chiedo a GitHub…");
    try { await api("POST", "/api/update/check", {}); renderUpdate(); checkUpdate(); }
    catch (e) { say(e.message || "Controllo non riuscito", true); chk.disabled = false; }
  };
  row.append(chk);
  const btns = [chk];
  if (s.available && !s.running){
    const go = el("button", "btn primary", "Aggiorna ora"); go.type = "button"; btns.push(go);
    go.onclick = () => runUpdate(btns, say, s.installed.sha);
    row.append(go);
  }
  box.append(row, msg);
  if (s.checked) box.append(el("p", "status", "Ultimo controllo: " + fmtWhen(s.checked)));
  if (s.running) runUpdate(btns, say, s.installed.sha, true);
}
async function runUpdate(btns, say, before, already){
  btns.forEach(b => b.disabled = true);
  if (!already){
    try { await api("POST", "/api/update/run", {}); }
    catch (e) { if (e.status !== 409){ say(e.message || "Aggiornamento non partito", true); btns.forEach(b => b.disabled = false); return; } }
  }
  say("Aggiornamento in corso: copia del database, download e riavvio. Non chiudere l'app…");
  const ov = $("updOverlay"), step = $("updStep"), bar = $("updBar");
  const show = (t, pct) => { step.textContent = t; bar.style.width = pct + "%"; };
  ov.className = "updover"; ov.hidden = false; show("Copio il database…", 12);
  const steps = [[3000, "Scarico la nuova versione…", 35], [7000, "Installo i file…", 58], [11000, "Riavvio la Dispensa…", 78]];
  const t0 = Date.now(); let down = false;
  const end = (t, bad) => { ov.hidden = true; say(t, bad); btns.forEach(b => b.disabled = false); };
  while (Date.now() - t0 < 120000){
    await new Promise(r => setTimeout(r, 1000));
    const spent = Date.now() - t0;
    if (!down) for (const [ms, t, pct] of steps) if (spent >= ms && parseFloat(bar.style.width) < pct) show(t, pct);
    let s;
    try { s = await api("GET", "/api/update"); }
    catch { down = true; show("Riavvio la Dispensa…", 85); continue; }   // il server si sta riavviando
    if (s.failed){ ov.classList.add("fail"); show("Non riuscito", 100); await new Promise(r => setTimeout(r, 1200));
      return end("Aggiornamento non riuscito: " + s.failed, true); }
    if (!s.running && (s.installed.sha !== before || !s.available)){
      $("updBanner").hidden = true;
      ov.classList.add("done"); show("Fatto! Riapro l'app…", 100);
      try { sessionStorage.setItem("dispensa-updated", "1"); } catch {}
      setTimeout(() => location.reload(), 1100); return;
    }
  }
  end("Il NAS non ha ancora finito: riapri l'app tra poco (i dettagli sono in update.log).", true);
}
// dopo il ricaricamento: conferma
try { if (sessionStorage.getItem("dispensa-updated")){ sessionStorage.removeItem("dispensa-updated"); setTimeout(() => toast("App aggiornata"), 400); } } catch {}
/* Controllo automatico: all'apertura e quando l'app torna in primo piano. Se c'è una versione nuova,
   la proprietaria vede una finestra (una volta per versione) e decide se aggiornare; il banner resta. */
const UPD_SEEN = "dispensa-upd-seen";
let updInfo = null, updChecked = 0;
async function checkUpdate(){
  updChecked = Date.now();
  let s; try { s = await api("GET", "/api/update"); } catch { return; }
  const can = s.owner && s.available && !s.running;
  $("updBanner").hidden = !can;
  updInfo = can ? s : null;
  if (s.owner && s.running) return runUpdate([], updSay, s.installed.sha, true);   // già in corso (es. da un altro telefono)
  if (can && ls.get(UPD_SEEN, "") !== s.latest.sha && $("login").hidden && !document.querySelector(".sheet:not([hidden])")) showUpdSheet();
}
function updSay(t, bad){ if (bad) toast(t); }
function showUpdSheet(){
  if (!updInfo) return;
  const l = updInfo.latest || {};
  const when = l.date ? new Date(l.date).toLocaleDateString("it-IT", { day: "numeric", month: "long" }) : "";
  $("updWhat").textContent = (l.message ? `Novità: ${l.message}` : "C'è una nuova versione su GitHub.") + (when ? ` (${when})` : "") +
    " Prima di aggiornare il NAS salva una copia del database.";
  ls.set(UPD_SEEN, l.sha);
  openSheet("updSheet");
}
$("updGo").onclick = () => { closeSheet("updSheet"); if (updInfo) runUpdate([], updSay, updInfo.installed.sha); };
$("updLater").onclick = () => { closeSheet("updSheet"); toast("Va bene: lo trovi nel banner in alto o in Menu › Aggiornamenti"); };
$("updSheet").onclick = e => { if (e.target.id === "updSheet") closeSheet("updSheet"); };
$("updBannerBtn").onclick = () => updInfo ? showUpdSheet() : $("menuBtn").click();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && me && Date.now() - updChecked > 60000) checkUpdate();
});

/* ---------------- aspetto (tema) ---------------- */
const PALETTES = {"oliva": {"label": "Oliva", "light": {"bg": "#f3f4ee", "card": "#fbfcf8", "fg": "#1f2419", "muted": "#5f6654", "line": "#d9dccf", "accent": "#3f5f22", "accent-fg": "#f6f8f0", "ok-bg": "#e3ebd5", "warn": "#8a5e00", "warn-bg": "#f6ebcc", "bad": "#a3361f", "bad-bg": "#f6dcd4"}, "dark": {"bg": "#15180f", "card": "#1e2217", "fg": "#e8ebe0", "muted": "#a3aa95", "line": "#323829", "accent": "#9cc06a", "accent-fg": "#16200b", "ok-bg": "#25301a", "warn": "#e2b04a", "warn-bg": "#3a2f12", "bad": "#ec8a72", "bad-bg": "#3d1d15"}}, "mare": {"label": "Mare", "light": {"bg": "#f2f5f8", "card": "#fbfcfe", "fg": "#16212b", "muted": "#55636f", "line": "#d5dde5", "accent": "#1f5f8b", "accent-fg": "#f4f8fb", "ok-bg": "#dfeaf3", "warn": "#8a5e00", "warn-bg": "#f6ebcc", "bad": "#a3361f", "bad-bg": "#f6dcd4"}, "dark": {"bg": "#0f161d", "card": "#17212b", "fg": "#e4ebf1", "muted": "#9aabbb", "line": "#2a3744", "accent": "#7db9e8", "accent-fg": "#0b1a26", "ok-bg": "#18293a", "warn": "#e2b04a", "warn-bg": "#3a2f12", "bad": "#ec8a72", "bad-bg": "#3d1d15"}}, "prugna": {"label": "Prugna", "light": {"bg": "#f6f3f6", "card": "#fdfbfd", "fg": "#241a24", "muted": "#675866", "line": "#e0d7df", "accent": "#6b3a6b", "accent-fg": "#faf5fa", "ok-bg": "#efe2ef", "warn": "#8a5e00", "warn-bg": "#f6ebcc", "bad": "#a3361f", "bad-bg": "#f6dcd4"}, "dark": {"bg": "#171217", "card": "#211a21", "fg": "#efe6ee", "muted": "#ad9dab", "line": "#3a2f39", "accent": "#d9a5d9", "accent-fg": "#2a102a", "ok-bg": "#33213a", "warn": "#e2b04a", "warn-bg": "#3a2f12", "bad": "#ec8a72", "bad-bg": "#3d1d15"}}, "sabbia": {"label": "Sabbia", "light": {"bg": "#f6f2ea", "card": "#fcfaf6", "fg": "#2a2219", "muted": "#6a5d4c", "line": "#e2d9c9", "accent": "#7a5410", "accent-fg": "#fdf8ef", "ok-bg": "#efe5cf", "warn": "#8a5e00", "warn-bg": "#f6ebcc", "bad": "#a3361f", "bad-bg": "#f6dcd4"}, "dark": {"bg": "#18150f", "card": "#221e17", "fg": "#efe8dc", "muted": "#aea18b", "line": "#3a3428", "accent": "#e5bd6e", "accent-fg": "#2a1c04", "ok-bg": "#33291a", "warn": "#e2b04a", "warn-bg": "#3a2f12", "bad": "#ec8a72", "bad-bg": "#3d1d15"}}, "contrasto": {"label": "Alto contrasto", "light": {"bg": "#ffffff", "card": "#ffffff", "fg": "#000000", "muted": "#333333", "line": "#6b6b6b", "accent": "#0039b3", "accent-fg": "#ffffff", "ok-bg": "#dfe8ff", "warn": "#6b4200", "warn-bg": "#fff1c2", "bad": "#9a0000", "bad-bg": "#ffe0e0"}, "dark": {"bg": "#000000", "card": "#0d0d0d", "fg": "#ffffff", "muted": "#d6d6d6", "line": "#8a8a8a", "accent": "#ffd60a", "accent-fg": "#000000", "ok-bg": "#2a2400", "warn": "#ffd60a", "warn-bg": "#332b00", "bad": "#ff8a80", "bad-bg": "#3a0000"}}};
const darkMQ = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : { matches: false };
function themePrefs(){ const t = ls.get("dispensa-theme", null) || {}; return { palette: PALETTES[t.palette] ? t.palette : "oliva", mode: ["auto","light","dark"].includes(t.mode) ? t.mode : "auto" }; }
function applyTheme(){
  const t = themePrefs(), dark = t.mode === "dark" || (t.mode === "auto" && darkMQ.matches);
  const vars = PALETTES[t.palette][dark ? "dark" : "light"], root = document.documentElement;
  for (const k in vars) root.style.setProperty("--" + k, vars[k]);
  root.style.colorScheme = dark ? "dark" : "light";
  root.toggleAttribute("data-dark", dark);
  const meta = document.getElementById("themeColor"); if (meta) meta.setAttribute("content", vars.bg);
  ls.set("dispensa-theme-vars", { vars, dark });
}
function renderPalettes(){
  const t = themePrefs(), box = $("palettes"); box.textContent = "";
  $("themeMode").value = t.mode;
  const dark = t.mode === "dark" || (t.mode === "auto" && darkMQ.matches);
  for (const [key, p] of Object.entries(PALETTES)){
    const v = p[dark ? "dark" : "light"];
    const b = el("button", "pal"); b.type = "button"; b.setAttribute("aria-pressed", key === t.palette);
    const sw = el("span", "sw");
    for (const c of [v.bg, v.accent, v.fg]){ const i = document.createElement("i"); i.style.background = c; sw.append(i); }
    b.append(sw, p.label);
    b.onclick = () => { ls.set("dispensa-theme", { ...themePrefs(), palette: key }); applyTheme(); renderPalettes(); };
    box.append(b);
  }
}
$("themeMode").onchange = e => { ls.set("dispensa-theme", { ...themePrefs(), mode: e.target.value }); applyTheme(); renderPalettes(); };
if (darkMQ.addEventListener) darkMQ.addEventListener("change", () => { applyTheme(); if (!$("menuView").hidden) renderPalettes(); });
applyTheme();

/* ---------------- scorri a sinistra per eliminare ---------------- */
function swipeable(card, it){
  const wrap = el("div", "swipe"), bg = el("div", "swipe-bg", "Elimina");
  wrap.append(bg, card);
  let x0 = null, y0 = 0, dx = 0, drag = false, pid = null;
  const limit = () => card.offsetWidth * 0.4;
  card.addEventListener("pointerdown", e => {
    if (e.button > 0) return;
    x0 = e.clientX; y0 = e.clientY; dx = 0; drag = false; pid = e.pointerId;
  });
  card.addEventListener("pointermove", e => {
    if (x0 === null || e.pointerId !== pid) return;
    const mx = e.clientX - x0, my = e.clientY - y0;
    if (!drag){
      if (mx < -10 && Math.abs(mx) > Math.abs(my) * 1.5){ drag = true; card.style.transition = "none"; try { card.setPointerCapture(pid); } catch {} }
      else if (Math.abs(my) > 10 || mx > 10){ x0 = null; return; }
      else return;
    }
    dx = Math.min(0, mx);
    card.style.transform = `translateX(${dx}px)`;
    bg.classList.toggle("armed", -dx > limit());
  });
  const end = () => {
    if (x0 === null) return;
    x0 = null; card.style.transition = "";
    if (!drag) return;
    drag = false; card.dataset.swiped = "1"; setTimeout(() => delete card.dataset.swiped, 60);
    if (-dx > limit()){ card.style.transform = "translateX(-110%)"; setTimeout(() => deleteWithUndo(it), 170); }
    else { card.style.transform = ""; bg.classList.remove("armed"); }
  };
  // dopo un trascinamento nessun clic (né sulla scheda né su + e −)
  card.addEventListener("click", e => { if (card.dataset.swiped){ e.stopPropagation(); e.preventDefault(); } }, true);
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
  return wrap;
}
function deleteWithUndo(it){
  const copy = { ...it };
  remove(it.id);
  toast(`“${it.name}” eliminato`, "Annulla", () => { upsert({ ...copy, updated: Date.now() }); toast("Ripristinato"); });
}

/* ---------------- avvisi di scadenza (notifiche) ---------------- */
for (let h = 6; h <= 22; h++){ const o = el("option", null, String(h).padStart(2, "0") + ":00"); o.value = h; $("n-hour").append(o); }
function pushSupport(){
  // PUSH_SUPPORT_CHECK
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (!window.isSecureContext) return "Le notifiche richiedono l'indirizzo https (fase 2, con Tailscale).";
  if (ios && !navigator.standalone) return "Apri la Dispensa dall'icona sulla schermata Home, poi attiva da lì.";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "Questo browser non supporta le notifiche (su iPhone serve iOS 16.4 o successivo).";
  return null;
}
function keyBytes(b64){ const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4)); return Uint8Array.from(s, c => c.charCodeAt(0)); }
async function currentSub(){ try { const reg = await navigator.serviceWorker.ready; return await reg.pushManager.getSubscription(); } catch { return null; } }
async function renderNotify(){
  const st = $("n-status"); st.className = "status"; st.textContent = "";
  try {
    const r = await api("GET", "/api/push/settings");
    $("n-days").value = String(r.days); $("n-hour").value = String(r.hour);
  } catch { st.textContent = "Impostazioni non disponibili: NAS non raggiungibile."; return; }
  const why = pushSupport();
  const sub = why ? null : await currentSub();
  $("n-on").textContent = sub ? "Disattiva su questo telefono" : "Attiva su questo telefono";
  $("n-on").classList.toggle("primary", !sub);
  $("n-test").hidden = !sub;
  st.textContent = why || (sub ? "Attive su questo telefono: un avviso al giorno, solo se qualcosa scade." : "Non attive su questo telefono.");
}
async function saveNotify(){
  try { await api("POST", "/api/push/settings", { days: Number($("n-days").value), hour: Number($("n-hour").value) }); toast("Avvisi aggiornati"); }
  catch (e) { toast(e.message || "Non salvato"); }
}
$("n-days").onchange = saveNotify;
$("n-hour").onchange = saveNotify;
$("n-on").onclick = async () => {
  const st = $("n-status"), why = pushSupport();
  if (why){ st.className = "status err"; st.textContent = why; return; }
  try {
    const old = await currentSub();
    if (old){
      await api("POST", "/api/push/unsubscribe", { endpoint: old.endpoint }).catch(() => {});
      await old.unsubscribe(); toast("Notifiche disattivate"); return renderNotify();
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted"){ st.className = "status err"; st.textContent = "Permesso negato: riattivalo in Impostazioni › Notifiche › Dispensa."; return; }
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api("GET", "/api/push/key");
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    await api("POST", "/api/push/subscribe", { subscription: sub.toJSON(), days: Number($("n-days").value), hour: Number($("n-hour").value) });
    toast("Notifiche attive"); renderNotify();
  } catch (e) { st.className = "status err"; st.textContent = "Attivazione non riuscita: " + (e.message || e); }
};
$("n-test").onclick = async () => {
  try { await api("POST", "/api/push/test", {}); toast("Prova inviata: arriva tra pochi secondi"); }
  catch (e) { toast(e.message || "Prova non riuscita"); }
};

/* ---------------- ricette ---------------- */
function soonAll(){ return items.filter(it => { const d = daysLeft(it.exp); return d !== null && d >= 0 && d <= 7 && Number(it.qty) > 0; }); }
const ASSISTANTS = {
  claude:  { label: "Claude",  url: q => "https://claude.ai/new?q=" + encodeURIComponent(q) },
  chatgpt: { label: "ChatGPT", url: q => "https://chatgpt.com/?q=" + encodeURIComponent(q) }
};
function assistant(){ const a = ls.get("dispensa-assistant", "claude"); return ASSISTANTS[a] ? a : "claude"; }
$("assistant").value = assistant();
$("assistant").onchange = e => { ls.set("dispensa-assistant", e.target.value); renderIdeas(); toast("Le ricette si chiederanno a " + ASSISTANTS[e.target.value].label); };
function renderIdeas(){
  const n = soonAll().length;
  $("ideas").hidden = !n;
  $("ideasBtn").textContent = aiOn ? "Idee ricette" : "Chiedi a " + ASSISTANTS[assistant()].label;
  $("assistantRow").hidden = aiOn;
  $("assistantNote").textContent = aiOn
    ? "Le ricette arrivano direttamente nell'app (AI collegata, vedi sopra)."
    : "Senza AI collegata, “Chiedi a…” apre l'app scelta con la domanda già scritta, usando il tuo account. Vale per questo telefono.";
  if (n) $("ideasText").textContent = n === 1 ? "1 prodotto scade entro 7 giorni" : `${n} prodotti scadono entro 7 giorni`;
}
let recipeMode = "ideas", savedIds = new Set();
async function loadSaved(){
  try { const r = await api("GET", "/api/saved-recipes"); savedIds = new Set(r.recipes.map(x => x.titolo)); return r.recipes; }
  catch { return null; }
}
function recipeCard(r, saved){
  const c = el("div", "recipe");
  c.append(el("h3", null, r.titolo));
  const meta = [r.tempo, saved && r.savedBy ? "salvata da " + r.savedBy : ""].filter(Boolean).join(" · ");
  if (meta) c.append(el("div", "rmeta", meta));
  if (r.perche) c.append(el("p", null, r.perche));
  if (r.usa && r.usa.length){
    const u = el("div", "uses"); const soonNames = new Set(soonAll().map(i => i.name.toLowerCase()));
    r.usa.forEach(n => u.append(el("span", soonNames.has(n.toLowerCase()) ? "" : "have", n)));
    c.append(u);
  }
  if (r.mancano && r.mancano.length) c.append(el("div", "missing", "Da comprare: " + r.mancano.join(", ")));
  if (r.passi && r.passi.length){
    const d = el("details"); d.append(el("summary", null, "Procedimento"));
    const ol = el("ol"); r.passi.forEach(p => ol.append(el("li", null, p))); d.append(ol); c.append(d);
  }
  const acts = el("div", "ractions");
  if (saved){
    const del = el("button", "btn danger", "Togli dal ricettario"); del.type = "button"; let armed = false;
    del.onclick = async () => {
      if (!armed){ armed = true; del.textContent = "Conferma"; return; }
      try { await api("DELETE", "/api/saved-recipes/" + encodeURIComponent(r.id)); c.remove(); toast("Tolta dal ricettario"); }
      catch (e) { toast(e.message || "Non tolta"); }
    };
    acts.append(del);
  } else {
    const sv = el("button", "btn", savedIds.has(r.titolo) ? "Nel ricettario" : "Salva nel ricettario"); sv.type = "button";
    sv.disabled = savedIds.has(r.titolo);
    sv.onclick = async () => {
      try { await api("PUT", "/api/saved-recipes/" + newId(), r); savedIds.add(r.titolo); sv.textContent = "Nel ricettario"; sv.disabled = true; toast("Salvata"); }
      catch (e) { toast(e.message || "Non salvata"); }
    };
    acts.append(sv);
  }
  c.append(acts);
  return c;
}
async function showIdeas(fresh){
  recipeMode = "ideas";
  $("recipeTitle").textContent = "Idee per cosa scade";
  const names = soonAll().sort((a, b) => daysLeft(a.exp) - daysLeft(b.exp)).map(i => i.name);
  $("recipeIntro").textContent = "Da usare presto: " + names.join(", ") + ". In giallo i prodotti in scadenza, in verde gli altri che hai già.";
  $("recipeMore").hidden = false; $("recipeMore").disabled = true;
  $("recipeList").textContent = ""; openSheet("recipeSheet");
  const st = $("recipeStatus"); st.className = "thinking"; st.textContent = "Sto pensando a qualche ricetta…";
  await loadSaved();
  try {
    const r = await api("POST", "/api/recipes", { nuove: !!fresh });
    st.className = ""; st.textContent = "";
    if (!r.ricette.length) st.textContent = "Nessuna idea questa volta: riprova con “Altre idee”.";
    r.ricette.forEach(x => $("recipeList").append(recipeCard(x, false)));
  } catch (e) {
    st.className = "status err"; st.textContent = e instanceof AuthError ? "Scegli prima chi sei." : (e.message || "Ricette non disponibili ora.");
  } finally { $("recipeMore").disabled = false; }
}
async function showBook(){
  recipeMode = "book";
  $("recipeTitle").textContent = "Ricettario";
  $("recipeIntro").textContent = "Le ricette che avete salvato.";
  $("recipeMore").hidden = true;
  $("recipeList").textContent = ""; openSheet("recipeSheet");
  const st = $("recipeStatus"); st.className = "thinking"; st.textContent = "Carico…";
  const list = await loadSaved();
  st.className = ""; st.textContent = "";
  if (!list){ st.className = "status err"; st.textContent = "Ricettario non disponibile: NAS non raggiungibile."; return; }
  if (!list.length){ st.className = "status"; st.textContent = "Ancora vuoto: dalle “Idee ricette” tocca “Salva nel ricettario”."; return; }
  list.forEach(x => $("recipeList").append(recipeCard(x, true)));
}
/* Senza chiave AI sul NAS: apre Claude (app o sito) con la domanda già scritta, usando il tuo account. */
function claudePrompt(){
  const soon = soonAll().sort((a, b) => daysLeft(a.exp) - daysLeft(b.exp));
  const soonIds = new Set(soon.map(i => i.id));
  const when = d => d === 0 ? "oggi" : d === 1 ? "domani" : `tra ${d} giorni`;
  const others = items.filter(i => !soonIds.has(i.id) && Number(i.qty) > 0 && (daysLeft(i.exp) ?? 0) >= 0).map(i => i.name);
  let text = "Proponi 3 ricette casalinghe per 2 persone che usino prima questi prodotti in scadenza: " +
    soon.map(i => `${i.name} (scade ${when(daysLeft(i.exp))})`).join(", ") + ".";
  let extra = "";
  for (const n of others){ if ((text + extra + n).length > 1500) break; extra += (extra ? ", " : "") + n; }
  if (extra) text += " In casa ho anche: " + extra + ".";
  return text + " Do per scontati sale, olio, pepe. Per ogni ricetta: tempo, ingredienti da comprare e passi brevi.";
}
$("ideasBtn").onclick = () => {
  if (aiOn) return showIdeas(false);
  const a = document.createElement("a");
  a.href = ASSISTANTS[assistant()].url(claudePrompt());
  a.target = "_blank"; a.rel = "noopener";
  document.body.append(a); a.click(); a.remove();
};
$("recipeMore").onclick = () => showIdeas(true);
$("recipeClose").onclick = () => closeSheet("recipeSheet");
$("recipeSheet").onclick = e => { if (e.target.id === "recipeSheet") closeSheet("recipeSheet"); };
$("bookBtn").onclick = showBook;

/* ---------------- scheda Ricette: spunti i prodotti, l'AI propone ---------------- */
const picked = new Set();
let pickQuery = "";
const usable = () => items.filter(i => Number(i.qty) > 0 && (daysLeft(i.exp) ?? 0) >= 0);
function renderPick(){
  const avail = usable(), ids = new Set(avail.map(i => i.id));
  for (const id of [...picked]) if (!ids.has(id)) picked.delete(id);     // finiti o scaduti nel frattempo
  const list = $("pickList"); list.textContent = "";
  const q = pickQuery.toLowerCase();
  const rows = avail.filter(i => !q || (i.name + " " + (i.cat || "")).toLowerCase().includes(q));
  $("pickInfo").textContent = avail.length ? `${avail.length} prodotti disponibili` : "";
  if (!avail.length) list.append(el("div", "empty", "Non ci sono prodotti disponibili: aggiungili dalla scheda Dispensa."));
  else if (!rows.length) list.append(el("div", "empty", "Nessun prodotto con questo nome."));
  for (const sp of spaces){
    const mine = rows.filter(i => spaceOf(i) === sp.id).sort((a, b) => a.name.localeCompare(b.name, "it"));
    if (!mine.length) continue;
    list.append(el("div", "pickgroup", sp.name));
    for (const it of mine){
      const lab = el("label", "pick" + (picked.has(it.id) ? " on" : ""));
      const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = picked.has(it.id);
      const tick = el("span", "tick");
      tick.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
      const pn = el("span", "pn"); pn.append(el("b", null, it.name));
      const d = daysLeft(it.exp), bits = [it.cat, d != null && d <= 7 ? (d === 0 ? "scade oggi" : d === 1 ? "scade domani" : `scade tra ${d} giorni`) : ""].filter(Boolean);
      if (bits.length) pn.append(el("small", null, bits.join(" · ")));
      cb.onchange = () => { cb.checked ? picked.add(it.id) : picked.delete(it.id); lab.classList.toggle("on", cb.checked); pickCount(); };
      lab.append(cb, tick, pn); list.append(lab);
    }
  }
  pickCount();
}
function pickCount(){
  const n = picked.size;
  $("pickCount").textContent = n === 1 ? "1 scelto" : `${n} scelti`;
  $("pickGo").disabled = !n; $("pickClear").disabled = !n;
  document.querySelector(".pickbar").classList.toggle("off", !n);     // compare solo quando hai scelto qualcosa
  $("pickGo").textContent = aiOn ? "Suggerisci" : "Chiedi a " + ASSISTANTS[assistant()].label;
}
$("pickQ").oninput = e => { pickQuery = e.target.value; renderPick(); };
$("pickClear").onclick = () => { picked.clear(); renderPick(); };
$("bookBtn2").onclick = showBook;
$("pickGo").onclick = async () => {
  const chosen = items.filter(i => picked.has(i.id)); if (!chosen.length) return;
  const wish = $("pickWish").value.trim();
  if (!aiOn){      // senza AI sul NAS: apre Claude/ChatGPT con la domanda già scritta
    const others = usable().filter(i => !picked.has(i.id)).map(i => i.name).slice(0, 40);
    let text = "Proponi 3 ricette casalinghe per 2 persone che usino questi prodotti: " + chosen.map(i => i.name).join(", ") + ".";
    if (others.length) text += " In casa ho anche: " + others.join(", ") + ".";
    if (wish) text += " Richieste: " + wish + ".";
    text += " Do per scontati sale, olio, pepe. Per ogni ricetta: tempo, ingredienti da comprare e passi brevi.";
    const a = document.createElement("a"); a.href = ASSISTANTS[assistant()].url(text); a.target = "_blank"; a.rel = "noopener";
    document.body.append(a); a.click(); a.remove(); return;
  }
  const out = $("pickResults"), btn = $("pickGo"); out.textContent = "";
  const st = el("div", "thinking", "Cerco qualche ricetta con quello che hai scelto…"); out.append(st);
  btn.disabled = true; st.scrollIntoView({ behavior: "smooth", block: "center" });
  try {
    await loadSaved();
    const r = await api("POST", "/api/recipes", { scelti: chosen.map(i => i.id), richiesta: wish });
    st.remove();
    if (!r.ricette.length) out.append(el("div", "status", "Nessuna idea questa volta: riprova o cambia i prodotti."));
    else { out.append(el("div", "section-label", "Ricette suggerite")); r.ricette.forEach(x => out.append(recipeCard(x, false))); }
    out.firstElementChild && out.firstElementChild.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    st.className = "status err"; st.textContent = e instanceof AuthError ? "Scegli prima chi sei." : (e.message || "Ricette non disponibili ora.");
  } finally { btn.disabled = !picked.size; }
};

/* ---------------- ambienti ---------------- */
function openSpaceSheet(){ $("s-name").value = ""; openSheet("spaceSheet"); setTimeout(() => $("s-name").focus(), 80); }
$("spaceCancel").onclick = () => closeSheet("spaceSheet");
$("spaceSheet").onclick = e => { if (e.target.id === "spaceSheet") closeSheet("spaceSheet"); };
$("spaceForm").onsubmit = async e => {
  e.preventDefault(); const name = $("s-name").value.trim(); if (!name) return;
  try {
    const r = await api("POST", "/api/spaces", { name });
    spaces.push({ id: r.id, name: r.name }); saveLocal();
    closeSheet("spaceSheet"); setCurrent(r.id); toast(r.name + " creato"); refresh();
  } catch (err) { toast(err instanceof AuthError ? "Scegli prima chi sei" : (err.message || "Serve la connessione al NAS")); }
};
async function renderSpaces(){
  const box = $("spaceList"); box.textContent = "";
  for (const sp of spaces){
    const row = el("div", "person");
    const inp = el("input"); inp.value = sp.name; inp.maxLength = 40; inp.setAttribute("aria-label", "Nome di " + sp.name);
    const save = el("button", "btn", "Rinomina"); save.type = "button";
    const del = el("button", "btn danger", "Rimuovi"); del.type = "button";
    save.onclick = async () => {
      const name = inp.value.trim(); if (!name || name === sp.name) return;
      try { await api("PATCH", "/api/spaces", { id: sp.id, name }); sp.name = name; saveLocal(); render(); renderSpaces(); toast("Rinominato"); }
      catch (e) { toast(e.message || "Non rinominato"); }
    };
    let armed = false;
    del.onclick = async () => {
      if (!armed){ armed = true; del.textContent = "Conferma"; return; }
      try {
        await api("DELETE", "/api/spaces/" + encodeURIComponent(sp.id));
        spaces = spaces.filter(s => s.id !== sp.id); saveLocal(); render(); renderSpaces(); toast(sp.name + " rimosso");
      } catch (e) { armed = false; del.textContent = "Rimuovi"; toast(e.message || "Non rimosso"); }
    };
    row.append(inp, save, del); box.append(row);
  }
  const add = el("button", "btn", "+ Nuovo ambiente"); add.type = "button";
  add.onclick = () => { showTab("home"); openSpaceSheet(); };
  box.append(add);
}

/* ---------------- persone (menu) ---------------- */
async function renderPeople(){
  const box = $("peopleList"); box.textContent = "";
  let users = [];
  try { users = (await api("GET", "/api/users")).users; }
  catch { box.append(el("p", null, "Persone non disponibili: NAS non raggiungibile.")); return; }
  for (const name of users){
    const row = el("div", "person");
    const inp = el("input"); inp.value = name; inp.maxLength = 40; inp.setAttribute("aria-label", "Nome di " + name);
    const save = el("button", "btn", "Rinomina"); save.type = "button";
    const del = el("button", "btn danger", "Rimuovi"); del.type = "button";
    save.onclick = async () => {
      const to = inp.value.trim(); if (!to || to === name) return;
      try { await api("PATCH", "/api/users", { from: name, to }); if (me === name) setMe(to); toast("Rinominato"); renderPeople(); refresh(); }
      catch (e) { toast(e.message || "Non rinominato"); }
    };
    let armed = false;
    del.onclick = async () => {
      if (!armed){ armed = true; del.textContent = "Conferma"; return; }
      try {
        await api("DELETE", "/api/users/" + encodeURIComponent(name)); toast(name + " rimosso");
        if (me === name){ setMe(null); showTab("home"); showLogin(); } else renderPeople();
      } catch (e) { toast(e.message || "Non rimosso"); }
    };
    row.append(inp, save, del); box.append(row);
  }
  const add = el("form", "addrow"); add.autocomplete = "off";
  const ni = el("input"); ni.placeholder = "Aggiungi una persona"; ni.maxLength = 40; ni.setAttribute("aria-label", "Nuova persona");
  const ab = el("button", "btn", "Aggiungi"); ab.type = "submit";
  add.append(ni, ab);
  add.onsubmit = async e => {
    e.preventDefault(); const name = ni.value.trim(); if (!name) return;
    try { await api("POST", "/api/users", { name }); toast("Aggiunto"); renderPeople(); } catch (err) { toast(err.message || "Non aggiunto"); }
  };
  box.append(add);
}

function setMe(name){ me = name; ls.set(ME_KEY, name); $("userChip").textContent = name || ""; $("userChip").hidden = !name; }
async function switchUser(){
  showTab("home");
  try { await api("POST", "/api/logout"); } catch {}
  setMe(null); showLogin();
}

/* ---------------- utilità ---------------- */
const DAY = 864e5;
function daysLeft(exp){ if (!exp) return null; const t = new Date(exp + "T00:00:00"), n = new Date(); n.setHours(0,0,0,0); return Math.round((t - n) / DAY); }
/* scheda di un prodotto; compact = una riga sottile (nome, pallino della scadenza, quantità) */
function itemCard(it, compact){
  if (compact) return rowCard(it);
  const card = el("div","item"); card.tabIndex = 0;
  const meta = el("div","meta");
  const tg = expTag(it); if (tg) meta.append(el("span","tag " + tg[0], tg[1]));
  if (it.cat) meta.append(el("span",null,it.cat));
  if (it.code) meta.append(el("span","code",it.code));
  if (it.updatedBy) meta.append(el("span",null,"agg. da " + it.updatedBy + (it.updated ? " · " + ago(it.updated) : "")));
  if (it.note){ const n = el("span",null, it.note.length > 40 ? it.note.slice(0,40) + "…" : it.note); n.style.fontStyle = "italic"; meta.append(n); }
  const qty = el("div","qty");
  const minus = el("button",null,"−"); minus.type = "button"; minus.setAttribute("aria-label","Togli uno");
  const plus = el("button",null,"+"); plus.type = "button"; plus.setAttribute("aria-label","Aggiungi uno");
  minus.onclick = e => { e.stopPropagation(); bump(it,-1); };
  plus.onclick = e => { e.stopPropagation(); bump(it, 1); };
  qty.append(minus, el("output",null,String(it.qty ?? 0)), plus);
  card.append(el("div","name",it.name), qty, meta);
  card.onclick = () => openForm(it);
  card.onkeydown = e => { if (e.key === "Enter") openForm(it); };
  return card;
}
function rowCard(it){
  const card = el("div", "item row"); card.tabIndex = 0;
  const tg = expTag(it);
  const dot = el("span", "dot " + (tg ? tg[0] : "none")); if (tg) dot.title = tg[1];
  const nm = el("div", "name", it.name);
  if (tg && tg[0] !== "ok") nm.append(el("small", null, " · " + tg[1].toLowerCase()));
  const qty = el("div", "qty");
  const minus = el("button", null, "−"); minus.type = "button"; minus.setAttribute("aria-label", "Togli uno");
  const plus = el("button", null, "+"); plus.type = "button"; plus.setAttribute("aria-label", "Aggiungi uno");
  minus.onclick = e => { e.stopPropagation(); bump(it, -1); };
  plus.onclick = e => { e.stopPropagation(); bump(it, 1); };
  qty.append(minus, el("output", null, String(it.qty ?? 0)), plus);
  card.append(dot, nm, qty);
  card.onclick = () => openForm(it);
  card.onkeydown = e => { if (e.key === "Enter") openForm(it); };
  return card;
}
/* vista (schede/righe) e "chiudi tutte / apri tutte" */
const VIEW_KEY = "dispensa-view", COLLAPSED_KEY = "dispensa-collapsed", NO_CAT = "Senza categoria";
let viewMode = ls.get(VIEW_KEY, "cards") === "rows" ? "rows" : "cards";
const collapsed = new Set(ls.get(COLLAPSED_KEY, []));
let lastOrder = [];
function renderListbar(order, canFold){
  if (order) lastOrder = order;
  const bar = $("listbar"); bar.hidden = !here().length;
  for (const b of bar.querySelectorAll(".seg button")) b.setAttribute("aria-pressed", b.dataset.v === viewMode);
  const fold = $("foldAll"), allClosed = lastOrder.length && lastOrder.every(c => collapsed.has(c));
  fold.hidden = canFold === false || lastOrder.length < 2;
  fold.textContent = allClosed ? "Apri tutte" : "Chiudi tutte";
}
for (const b of document.querySelectorAll("#listbar .seg button"))
  b.onclick = () => { viewMode = b.dataset.v; ls.set(VIEW_KEY, viewMode); render(); };
$("foldAll").onclick = () => {
  const allClosed = lastOrder.every(c => collapsed.has(c));
  for (const c of lastOrder) allClosed ? collapsed.delete(c) : collapsed.add(c);
  ls.set(COLLAPSED_KEY, [...collapsed]); render();
};
function expTag(it){
  if (Number(it.qty) <= 0) return ["out","Finito"];
  const d = daysLeft(it.exp); if (d === null) return null;
  if (d < 0) return ["bad","Scaduto"];
  if (d === 0) return ["bad","Scade oggi"];
  if (d <= 7) return ["warn", d === 1 ? "Scade domani" : `Scade tra ${d} gg`];
  return ["ok", new Date(it.exp + "T00:00:00").toLocaleDateString("it-IT",{day:"2-digit",month:"short",year:"2-digit"})];
}
function toast(t, actionLabel, action){
  const box = $("toast"); box.textContent = ""; box.append(document.createTextNode(t));
  if (actionLabel){ const b = document.createElement("button"); b.type = "button"; b.textContent = actionLabel;
    b.onclick = () => { box.hidden = true; action(); }; box.append(b); }
  box.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => box.hidden = true, actionLabel ? 5000 : 2400);
}
function ago(ms){
  const d = Math.floor((Date.now() - ms) / DAY);
  return d <= 0 ? "oggi" : d === 1 ? "ieri" : d < 30 ? `${d} gg fa` : new Date(ms).toLocaleDateString("it-IT",{day:"numeric",month:"short"});
}
function newId(){ return Date.now().toString(36) + Math.random().toString(36).slice(2,7); }
function el(tag, cls, text){ const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

/* ---------------- lista ---------------- */
function here(){ const sid = curSpace().id; return items.filter(it => spaceOf(it) === sid); }
function expiring(list){ return list.filter(it => { const d = daysLeft(it.exp); return d !== null && d <= 7 && Number(it.qty) > 0; }).length; }
function renderTabs(){
  const box = $("tabs"); box.textContent = "";
  const cur = curSpace().id;
  for (const sp of spaces){
    const b = el("button", "tab"); b.type = "button"; b.setAttribute("role", "tab"); b.setAttribute("aria-selected", sp.id === cur);
    b.append(sp.name);
    const n = expiring(items.filter(it => spaceOf(it) === sp.id));
    if (n){ const bd = el("span", "badge", String(n)); bd.title = n + " in scadenza"; b.append(bd); }
    b.onclick = () => setCurrent(sp.id);
    box.append(b);
  }
  const add = el("button", "tab add", "+ Ambiente"); add.type = "button"; add.onclick = openSpaceSheet;
  box.append(add);
}
function visible(){
  const q = query.trim().toLowerCase();
  const key = it => Number(it.qty) <= 0 ? 1e6 : (daysLeft(it.exp) ?? 1e5);
  return here().filter(it => {
    if (q && !`${it.name} ${it.note||""} ${it.cat||""} ${it.code||""}`.toLowerCase().includes(q)) return false;
    if (filter === "Tutti") return true;
    if (filter === "Finiti") return Number(it.qty) <= 0;
    if (filter === "In scadenza") { const d = daysLeft(it.exp); return d !== null && d <= 7 && Number(it.qty) > 0; }
    return (it.cat || "") === filter;
  }).sort((a,b) => key(a) - key(b) || a.name.localeCompare(b.name, "it"));
}
function render(){
  renderTabs(); renderFab();
  if (tab === "recipes") renderPick();
  const sp = curSpace(), mine = here();
  $("appTitle").textContent = sp.name;
  const cats = [...new Set(mine.map(i => i.cat).filter(Boolean))].sort((a, b) => a.localeCompare(b, "it"));
  if (!["Tutti","In scadenza","Finiti", ...cats].includes(filter)) filter = "Tutti";
  const chips = $("chips"); chips.textContent = "";
  for (const f of ["Tutti","In scadenza","Finiti", ...cats]){
    const b = el("button","chip",f); b.type = "button"; b.setAttribute("aria-pressed", f === filter);
    b.onclick = () => { filter = f; render(); }; chips.append(b);
  }
  const avail = mine.filter(i => Number(i.qty) > 0).length;
  $("count").textContent = mine.length ? `${avail} disponibili · ${mine.length} prodotti` : "";
  const dl = $("cats"); dl.textContent = "";
  [...new Set(items.map(i => i.cat).filter(Boolean))].forEach(c => { const o = el("option"); o.value = c; dl.append(o); });

  renderIdeas();
  const list = $("list"); list.textContent = "";
  if (!mine.length){
    const e = el("div","empty"); e.append(el("strong",null,`Niente in ${sp.name}`),
      (aiOn ? `Tocca il pulsante rotondo in basso e fotografa il prodotto: compilo io la scheda e finisce in ${sp.name}.`
             : `Tocca il pulsante rotondo in basso e inquadra il codice a barre: il prodotto finirà in ${sp.name}.`) + " Con “+” in alto lo scrivi a mano.");
    list.append(e); renderListbar([], false); renderBanner(); return;
  }
  const rows = visible();
  if (!rows.length){ list.append(el("div","empty","Nessun prodotto per questo filtro.")); renderListbar([], false); renderBanner(); return; }
  // sezioni per categoria, richiudibili; con una ricerca in corso restano tutte aperte
  const compact = viewMode === "rows", searching = !!query.trim();
  const groups = new Map();
  for (const it of rows){ const c = (it.cat || "").trim() || NO_CAT; if (!groups.has(c)) groups.set(c, []); groups.get(c).push(it); }
  const order = [...groups.keys()].sort((a, b) => a === NO_CAT ? 1 : b === NO_CAT ? -1 : a.localeCompare(b, "it"));
  const headers = !(order.length === 1 && filter !== "Tutti");      // filtro su una categoria: niente intestazione
  for (const c of order){
    const its = groups.get(c), closed = headers && !searching && collapsed.has(c);
    const sec = el("section", "group" + (closed ? " closed" : ""));
    if (headers){
      const h = el("button", "ghead"); h.type = "button"; h.setAttribute("aria-expanded", !closed);
      h.innerHTML = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
      h.append(el("span", "gname", c), el("span", "gcount", String(its.length)));
      const warn = expiring(its); if (warn){ const b = el("span", "badge", String(warn)); b.title = warn + " in scadenza"; h.append(b); }
      h.onclick = () => {
        if (searching) return;
        const now = !sec.classList.toggle("closed");
        now ? collapsed.delete(c) : collapsed.add(c);
        h.setAttribute("aria-expanded", now); ls.set(COLLAPSED_KEY, [...collapsed]); renderListbar();
      };
      sec.append(h);
    }
    const body = el("div", "gbody" + (compact ? " compact" : ""));
    for (const it of its) body.append(swipeable(itemCard(it, compact), it));
    sec.append(body); list.append(sec);
  }
  renderListbar(order, headers && !searching);
  renderBanner();
}
function bump(it, d){
  const q = Math.max(0, (Number(it.qty) || 0) + d);
  if (q !== Number(it.qty)) upsert({ ...it, qty: q, updated: Date.now() });
}

/* ---------------- scheda prodotto ---------------- */
function openForm(it, code, template, note, noFocus, instant){
  editing = it || null;
  const t = template || {};
  $("sheetTitle").textContent = it ? "Modifica prodotto" : "Nuovo prodotto";
  $("sheetCode").textContent = note || (!it && code ? "Codice letto: " + code : "");
  const sel = $("f-space"); sel.textContent = "";
  for (const sp of spaces){ const o = el("option", null, sp.name); o.value = sp.id; sel.append(o); }
  sel.value = it ? spaceOf(it) : curSpace().id;
  $("f-name").value = it?.name || t.name || "";
  $("f-code").value = it?.code || code || "";
  $("f-qty").value = it?.qty ?? 1;
  $("f-exp").value = it?.exp || "";
  $("f-cat").value = it?.cat || t.cat || "";
  $("f-note").value = it?.note || t.note || "";
  $("readDate").hidden = !aiOn; $("readMsg").hidden = true;
  $("recRow").hidden = !aiOn || !!it; $("recMsg").hidden = true; $("recBtn").disabled = false;
  $("delRow").hidden = !it; delArmed = false; $("delBtn").textContent = "Elimina prodotto";
  openSheet("sheet", instant);
  if (!it && !noFocus) setTimeout(() => $("f-name").focus(), 80);
}
function closeForm(){ closeSheet("sheet"); editing = null; }
sheetClosers.sheet = closeForm;
$("cancelBtn").onclick = closeForm;
$("sheet").onclick = e => { if (e.target.id === "sheet") closeForm(); };
$("form").noValidate = true;     // la verifica di Safari nella web app può bloccare senza dire niente
$("form").onsubmit = e => {
  e.preventDefault();
  try { saveForm(); }
  catch (err) { console.error(err); toast("Non salvato: " + (err.message || "errore imprevisto")); }
};
function saveForm(){
  if (!$("f-name").value.trim()){ toast("Scrivi il nome del prodotto"); $("f-name").focus(); return; }
  const it = {
    id: editing ? editing.id : newId(),
    code: $("f-code").value.trim(), name: $("f-name").value.trim(), space: $("f-space").value,
    qty: Math.max(0, Number($("f-qty").value) || 0), exp: $("f-exp").value,
    cat: $("f-cat").value.trim(), note: $("f-note").value.trim(), updated: Date.now()
  };
  if (!it.name) return;
  const same = byCode(it.code, editing && editing.id);
  if (same && editing){ toast(`Il codice ${it.code} è già di “${same.name}”: un codice = un solo prodotto`); $("f-code").focus(); return; }
  if (same){        // codice scritto a mano che esiste già: aggiungo le confezioni a quel prodotto
    const n = (Number(same.qty) || 0) + it.qty;
    upsert({ ...same, qty: n, exp: it.exp || same.exp, updated: Date.now() }); closeForm();
    toast(`Aggiunto a “${same.name}”: ora ${n}`); return;
  }
  const moved = editing && spaceOf(editing) !== it.space;
  upsert(it); closeForm();
  toast(moved ? "Spostato in " + (spaces.find(s => s.id === it.space) || {}).name : "Salvato");
}
let delArmed = false;
$("delBtn").onclick = () => {
  if (!delArmed){ delArmed = true; $("delBtn").textContent = "Tocca di nuovo per eliminare"; return; }
  remove(editing.id); closeForm(); toast("Eliminato");
};
$("addBtn").onclick = () => openForm(null, "");
$("q").oninput = e => { query = e.target.value; render(); };

/* codice letto dal pulsante accanto a "Barcode" nella scheda: va nella scheda già aperta */
let scanIntoForm = false;
const formOpenNew = () => !$("sheet").hidden && !$("sheet").classList.contains("closing") && !editing;
/* ---- il codice a barre identifica il prodotto, in tutti gli ambienti: un codice = una sola voce ----
   Se il codice c'è già, non si crea un doppione: si riapre quella voce come "nuova confezione"
   (quantità +1, scadenza della nuova confezione) e si tengono nome, categoria, note e ambiente. */
function byCode(code, exceptId){
  code = String(code || "").trim(); if (!code) return null;
  const all = items.filter(x => x.code === code && x.id !== exceptId);
  return all.find(x => spaceOf(x) === curSpace().id) || all[0] || null;
}
const spaceName = it => (spaces.find(s => s.id === spaceOf(it)) || {}).name || "un altro ambiente";
function openRestock(found, exp, instant){
  openForm(found, "", null, "", true, instant);
  const n = (Number(found.qty) || 0) + 1;
  $("f-qty").value = n;
  if (exp) $("f-exp").value = exp;
  $("sheetTitle").textContent = "Nuova confezione";
  const where = spaceOf(found) !== curSpace().id ? ` in ${spaceName(found)}` : "";
  $("sheetCode").textContent = `Hai già “${found.name}”${where}: quantità portata a ${n}. ` +
    (exp ? `Scadenza della nuova confezione: ${fmtDate(exp)}, controllala.` : "Aggiorna la scadenza con quella della nuova confezione.");
}
$("scanCode").onclick = () => { scanIntoForm = true; startScan(); };
function handleCode(code){
  code = String(code).trim(); if (!code) return;
  const intoForm = scanIntoForm && formOpenNew(); scanIntoForm = false;
  const found = byCode(code);
  if (found){ openRestock(found, intoForm ? $("f-exp").value : ""); return; }
  if (intoForm){
    $("f-code").value = code;
    $("sheetCode").textContent = "Codice letto: " + code + ". Cerco il nome…";
    lookupProduct(code);
    return;
  }
  openForm(null, code, null, "Codice letto: " + code + ". Cerco il nome…"); lookupProduct(code);
}
/* Nome, formato e categoria da Open Food Facts (il NAS fa da tramite e tiene una cache). */
async function lookupProduct(code){
  const still = () => formOpenNew() && $("f-code").value.trim() === code;
  let r = null;
  try { r = await api("GET", "/api/product/" + encodeURIComponent(code)); } catch {}
  if (!still()) return;
  const note = $("sheetCode");
  if (r && r.found){
    if (!$("f-name").value.trim()) $("f-name").value = r.name;
    if (!$("f-cat").value.trim() && r.cat) $("f-cat").value = sameCat(r.cat);
    note.textContent = "Trovato su Open Food Facts: controlla il nome e aggiungi la scadenza.";
  } else {
    note.textContent = "Codice letto: " + code + ". " + (r ? "Prodotto non presente su Open Food Facts" : "Ricerca del nome non riuscita") +
      (aiOn ? ": prova “Riconosci dalla foto”." : ": scrivi il nome.");
  }
}
/* usa la stessa grafia di una categoria già presente (es. "pasta" → "Pasta") */
function sameCat(c){
  const low = c.toLowerCase(), hit = items.find(i => (i.cat || "").toLowerCase() === low);
  return hit ? hit.cat : c;
}

/* ---------------- decodifica ---------------- */
const Z = window.ZXing;
let reader = null, hints = null;
if (Z){
  hints = new Map();
  hints.set(Z.DecodeHintType.TRY_HARDER, true);
  hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E, Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.ITF, Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.DATA_MATRIX]);
  reader = new Z.MultiFormatReader(); reader.setHints(hints);
}
const nativeDetector = ("BarcodeDetector" in window) ? (() => { try { return new BarcodeDetector(); } catch { return null; } })() : null;

function decodeCanvas(c){
  if (!reader) return null;
  try {
    const bmp = new Z.BinaryBitmap(new Z.HybridBinarizer(new Z.HTMLCanvasElementLuminanceSource(c)));
    const r = reader.decodeWithState(bmp);
    return r ? r.getText() : null;
  } catch { return null; } finally { try { reader.reset(); } catch {} }
}

/* ---------------- scanner dal vivo ---------------- */
let stream = null, running = false, lastTry = 0, torchOn = false;
const work = document.createElement("canvas");
const wctx = work.getContext("2d", { willReadFrequently: true });

$("scanBtn").onclick = () => {
  if (!aiOn){ scanIntoForm = false; return startScan(); }
  // prima la fotocamera; la scheda si apre quando la foto è pronta (vedi recPhoto.onchange)
  recFromFab = true; $("recPhoto").value = ""; $("recPhoto").click();
};
/* il pulsante rotondo riconosce il prodotto con l'AI; senza AI resta lo scanner del codice */
function renderFab(){
  const b = $("scanBtn");
  b.querySelector(".ico-scan").toggleAttribute("hidden", aiOn);
  b.querySelector(".ico-rec").toggleAttribute("hidden", !aiOn);
  b.setAttribute("aria-label", aiOn ? "Riconosci un prodotto dalla foto" : "Scansiona un codice a barre");
}
$("closeScan").onclick = stopScan;
$("photoBtn").onclick = () => { stopScan(); $("photo").value = ""; $("photo").click(); };
$("typeBtn").onclick = () => { stopScan(); openCodeSheet("Scrivi le cifre stampate sotto il barcode."); };

async function startScan(){
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    $("photo").value = ""; $("photo").click(); return;
  }
  $("scanner").hidden = false; $("scanHint").textContent = "Inquadra il barcode dentro il riquadro";
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
  } catch (e) {
    $("scanner").hidden = true;
    openCodeSheet("Non ho accesso alla fotocamera. Consentila in Impostazioni › Safari › Fotocamera, oppure scrivi il codice qui.");
    return;
  }
  const v = $("video"); v.srcObject = stream;
  try { await v.play(); } catch {}
  const track = stream.getVideoTracks()[0];
  try {
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    $("torchBtn").hidden = !caps.torch;
    if (caps.focusMode && caps.focusMode.includes("continuous")) track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(()=>{});
  } catch { $("torchBtn").hidden = true; }
  running = true; requestAnimationFrame(loop);
}
$("torchBtn").onclick = () => {
  const t = stream && stream.getVideoTracks()[0]; if (!t) return;
  torchOn = !torchOn; t.applyConstraints({ advanced: [{ torch: torchOn }] }).catch(()=>{});
};
function stopScan(){
  running = false; torchOn = false;
  if (stream){ stream.getTracks().forEach(t => t.stop()); stream = null; }
  $("video").srcObject = null; $("scanner").hidden = true;
}
async function loop(ts){
  if (!running) return;
  const v = $("video");
  if (ts - lastTry > 120 && v.readyState >= 2 && v.videoWidth){
    lastTry = ts;
    const code = await tryFrame(v);
    if (code && running){
      if (navigator.vibrate) navigator.vibrate(60);
      stopScan(); handleCode(code); return;
    }
  }
  requestAnimationFrame(loop);
}
async function tryFrame(v){
  if (nativeDetector){
    try { const r = await nativeDetector.detect(v); if (r && r[0] && r[0].rawValue) return r[0].rawValue; } catch {}
  }
  // ritaglio centrale (fascia orizzontale), ridotto a ~900px di larghezza
  const vw = v.videoWidth, vh = v.videoHeight;
  const sw = Math.round(vw * 0.85), sh = Math.round(vh * 0.45);
  const sx = Math.round((vw - sw) / 2), sy = Math.round(vh * 0.42 - sh / 2);
  const scale = Math.min(1, 900 / sw);
  work.width = Math.round(sw * scale); work.height = Math.round(sh * scale);
  wctx.drawImage(v, sx, Math.max(0, sy), sw, sh, 0, 0, work.width, work.height);
  return decodeCanvas(work);
}

/* ---------------- da foto ---------------- */
$("photo").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  toast("Sto leggendo la foto…");
  const code = await decodePhoto(f).catch(() => null);
  if (code) handleCode(code);
  else openCodeSheet("Non riesco a leggere il codice dalla foto. Scrivi le cifre stampate sotto il barcode.");
};
async function decodePhoto(file){
  const url = URL.createObjectURL(file);
  const img = new Image(); img.src = url;
  try { await img.decode(); } finally { setTimeout(() => URL.revokeObjectURL(url), 1000); }
  if (nativeDetector){ try { const r = await nativeDetector.detect(img); if (r[0]) return r[0].rawValue; } catch {} }
  for (const [side, rot] of [[1400,false],[900,false],[1400,true],[2000,false],[900,true]]){
    await new Promise(r => setTimeout(r, 0));
    const s = Math.min(1, side / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.round(img.naturalWidth * s), h = Math.round(img.naturalHeight * s);
    work.width = rot ? h : w; work.height = rot ? w : h;
    wctx.save(); if (rot){ wctx.translate(h, 0); wctx.rotate(Math.PI / 2); }
    wctx.drawImage(img, 0, 0, w, h); wctx.restore();
    const c = decodeCanvas(work); if (c) return c;
  }
  return null;
}

/* ---------------- codice a mano ---------------- */
function openCodeSheet(msg){ $("codeMsg").textContent = msg; $("m-code").value = ""; openSheet("codeSheet"); setTimeout(() => $("m-code").focus(), 80); }
$("codeCancel").onclick = () => closeSheet("codeSheet");
$("codeSheet").onclick = e => { if (e.target.id === "codeSheet") closeSheet("codeSheet"); };
$("codeForm").onsubmit = e => { e.preventDefault(); const c = $("m-code").value.trim(); if (!c) return; closeSheet("codeSheet"); handleCode(c); };

/* ---------------- backup ---------------- */
function lastBackup(){ try { return Number(localStorage.getItem(BK)) || 0; } catch { return 0; } }
function renderBanner(){
  const lb = lastBackup(), age = (Date.now() - lb) / DAY;
  const show = items.length >= 5 && (!lb || age > 30);
  $("backupBanner").hidden = !show;
  if (show) $("backupText").textContent = lb ? `Ultimo backup ${Math.floor(age)} giorni fa.` : "Non hai ancora fatto un backup.";
}
$("backupNow").onclick = exportData;
$("menuBtn").onclick = () => {
  if (tab === "menu") return toTop();
  const lb = lastBackup();
  $("lastBackup").textContent = lb ? "Ultimo backup: " + new Date(lb).toLocaleDateString("it-IT",{day:"numeric",month:"long",year:"numeric"}) : "Nessun backup finora.";
  showTab("menu");
  renderPalettes(); renderAI(); renderUpdate(); renderNotify(); renderSpaces(); renderPeople();
};
$("switchUser").onclick = switchUser;
$("userChip").onclick = switchUser;
$("exportBtn").onclick = exportData;
$("importBtn").onclick = () => { $("importFile").value = ""; $("importFile").click(); };

async function exportData(){
  const stamp = new Date().toISOString().slice(0,10);
  const json = JSON.stringify({ app: "dispensa", version: 1, exported: new Date().toISOString(), items }, null, 2);
  const file = new File([json], `dispensa-${stamp}.json`, { type: "application/json" });
  const done = () => { try { localStorage.setItem(BK, String(Date.now())); } catch {} renderBanner(); showTab("home"); };
  if (navigator.canShare && navigator.canShare({ files: [file] })){
    try { await navigator.share({ files: [file], title: "Backup dispensa" }); done(); toast("Backup esportato"); return; }
    catch (e) { if (e && e.name === "AbortError") return; }
  }
  const a = document.createElement("a"); a.href = URL.createObjectURL(file); a.download = file.name;
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  done(); toast("Backup scaricato");
}
$("importFile").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  try {
    const data = JSON.parse(await f.text());
    const incoming = Array.isArray(data) ? data : data.items;
    if (!Array.isArray(incoming)) throw 0;
    let added = 0, updated = 0;
    for (const it of incoming){
      if (!it || !it.id || !it.name) continue;
      const cur = items.find(x => x.id === it.id);
      const id = /^[A-Za-z0-9_-]{1,64}$/.test(it.id) ? it.id : newId();
      if (!it.space || !spaces.some(s => s.id === it.space)){
        const byName = spaces.find(s => s.name.toLowerCase() === String(it.place || "").toLowerCase());
        it.space = byName ? byName.id : curSpace().id;
      }
      if (!cur){ upsert({ ...it, id }); added++; }
      else if ((it.updated || 0) > (cur.updated || 0)){ upsert({ ...cur, ...it, id: cur.id }); updated++; }
    }
    render(); showTab("home");
    toast(`Importati: ${added} nuovi, ${updated} aggiornati`);
  } catch { toast("File non valido: scegli un backup .json della Dispensa"); }
};

/* ---------------- avvio ---------------- */
setMe(me); render();
(async () => {
  try {
    const r = await api("GET", "/api/me");
    setMe(r.name); refresh(); checkAIExpiry(); checkUpdate();
  } catch (e) {
    if (e instanceof AuthError) showLogin();
    else if (!me) showLogin();             // mai entrato e NAS irraggiungibile: mostra il messaggio
    else setOnline(false);                 // già riconosciuto: lavora sulla copia locale
  }
})();
setInterval(() => { if (!document.hidden && me) refresh(); }, 20000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && me) refresh(); });
window.addEventListener("online", () => { if (me) refresh(); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(()=>{});
document.addEventListener("visibilitychange", () => { if (document.hidden && running) stopScan(); });
})();
