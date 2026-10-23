"use strict";
// Kirka farm panel — vanilla JS over /api/v1/farm/*. Data: CROFT (read-only), refresh 60 s.
const API = "/api/v1";
const TOKENS = "kirkalab_tokens";
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (x, d = 0) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }));
const MSK = { timeZone: "Europe/Moscow" };
const tfmt = (iso, withDate = true) => { if (!iso) return "—"; const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso); return d.toLocaleString("ru-RU", Object.assign({ hour: "2-digit", minute: "2-digit" }, withDate ? { day: "2-digit", month: "2-digit" } : {}, MSK)); };
const dur = (sec) => { if (sec == null) return "—"; sec = Math.max(0, Math.floor(sec)); const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60); return d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`; };
const ST = { working: "Работает", offline: "Офлайн", suspended: "Пауза", unknown: "Нет данных" };
const LV = { 0: "Инфо", 1: "Предупреждение", 2: "Опасно", 3: "Критично" };
const CLS = { offline: "Нет связи", power: "Питание", temperature: "Температура", hashrate: "Хешрейт", hardware: "Аппаратные", network: "Сеть и пул", config: "Конфигурация", pressure: "Давление гидро", other: "Прочее" };

// ---------- auth ----------
const tok = { get() { try { return JSON.parse(localStorage.getItem(TOKENS)); } catch (e) { return null; } }, set(t) { localStorage.setItem(TOKENS, JSON.stringify(t)); }, clear() { localStorage.removeItem(TOKENS); } };
async function api(path, opts = {}, retry) {
  opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const t = tok.get();
  if (t && t.access_token) opts.headers.Authorization = "Bearer " + t.access_token;
  const r = await fetch(API + path, opts);
  if (r.status === 401 && !retry && t && t.refresh_token) {
    const rr = await fetch(API + "/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: t.refresh_token }) });
    if (rr.ok) { tok.set(await rr.json()); return api(path, opts, true); }
  }
  if (r.status === 401 || r.status === 403) { const e = new Error("auth"); e.status = r.status; throw e; }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
let qrTimer = null;
async function showLogin(msg) {
  $("#app").hidden = true; $("#login").hidden = false; $("#lgmsg").textContent = msg || "";
  try {
    const r = await fetch(API + "/auth/qr/start", { method: "POST" });
    const s = await r.json();
    $("#tglink").href = s.deep_link;
    const qr = qrcode(0, "M"); qr.addData(s.deep_link); qr.make();
    $("#qrbox").innerHTML = qr.createSvgTag({ cellSize: 4, margin: 0, scalable: true });
    clearInterval(qrTimer);
    qrTimer = setInterval(async () => {
      const st = await (await fetch(API + "/auth/qr/status/" + encodeURIComponent(s.session_id))).json();
      if (st.status === "approved" && st.access_token) { clearInterval(qrTimer); tok.set(st); boot(); }
      else if (st.status === "expired" || st.status === "consumed") { clearInterval(qrTimer); showLogin("Код устарел — показала новый."); }
    }, 2000);
  } catch (e) { $("#lgmsg").textContent = "Сервер не отвечает, попробуйте обновить страницу."; }
}

// ---------- state ----------
const S = { page: "today", sum: null, devs: null, devAt: 0, sel: new Set(), lastIdx: null, order: [],
  f: { q: "", status: "", cls: "", container: "", model: "", alarms: false, sort: "container" },
  ef: { cls: "", container: "", model: "", group: "code", q: "", level: "" } };
const site = () => $("#siteSel").value, client = () => $("#clientSel").value;
const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== false).map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&");

async function loadSummary() {
  S.sum = await api("/farm/summary");
  const s = S.sum;
  const ss = $("#siteSel"), cs = $("#clientSel"), sv = ss.value, cv = cs.value;
  ss.innerHTML = '<option value="">Все площадки</option>' + s.sites.map((x) => `<option value="${x.id}">${esc(x.name.trim())}</option>`).join("");
  cs.innerHTML = '<option value="">Все клиенты</option>' + s.clients.filter((c) => c.id).map((c) => `<option value="${c.id}">${esc(c.name.trim())} · ${c.total}</option>`).join("");
  ss.value = sv; cs.value = cv;
  const age = s.synced_at ? (Date.now() - new Date(s.synced_at)) / 1000 : 1e9;
  $("#syncAt").textContent = s.synced_at ? "CROFT · " + tfmt(s.synced_at, false) : "нет данных";
  $(".auto").classList.toggle("stale", age > 300);
  const b = $("#errBadge"); b.hidden = !s.with_alarms; b.textContent = s.with_alarms > 999 ? "999+" : s.with_alarms;
}
async function loadDevices(force) {
  if (!force && S.devs && Date.now() - S.devAt < 55000) return S.devs;
  const r = await api("/farm/devices?" + qs({ site: site(), client: client(), limit: 6000 }));
  S.devs = r.items; S.devAt = Date.now();
  return S.devs;
}

// ---------- pages ----------
const PAGES = { today: pageToday, map: pageMap, asics: pageAsics, errors: pageErrors, clients: pageClients };
const TITLES = { today: "Сегодня", map: "Карта контейнеров", asics: "Асики", errors: "Ошибки", clients: "Клиенты" };
async function go(p, keep) {
  S.page = p; if (!keep) { S.sel.clear(); updSel(); }
  $$(".nav[data-page]").forEach((n) => n.classList.toggle("on", n.dataset.page === p));
  $("#pageTitle").textContent = TITLES[p];
  history.replaceState(null, "", "#" + p);
  try { await PAGES[p](); } catch (e) { if (S.page !== p) return; if (e.message === "auth") return showLogin(e.status === 403 ? "Ферма доступна администраторам. Войдите под нужным Telegram." : ""); $("#main").innerHTML = `<div class="empty">Не удалось загрузить: ${esc(e.message)}</div>`; }
}

function filtSum() {
  const s = S.sum; const sid = site() ? +site() : null;
  const conts = s.containers.filter((c) => !sid || c.site_id === sid);
  return { conts };
}

async function pageToday() {
  await loadSummary();
  const s = S.sum, { conts } = filtSum();
  const devs = site() || client() ? await loadDevices(true) : null;
  const agg = devs ? devs.reduce((a, d) => { a.total++; a[d.status] = (a[d.status] || 0) + 1; if (d.status === "working") { a.ths += d.ths || 0; a.nom += d.nominal || 0; } a.kw += (d.w || 0) / 1000; if (d.alarms.length) a.al++; return a; }, { total: 0, ths: 0, nom: 0, kw: 0, al: 0 }) : { total: s.total, working: s.working, offline: s.offline, suspended: s.suspended, ths: s.ths, nom: s.nominal_ths, kw: s.kw, al: s.with_alarms };
  const eff = agg.nom ? agg.ths / agg.nom * 100 : null;
  const pw = (x) => (agg.total ? (x || 0) / agg.total * 100 : 0);
  const openInc = s.incidents.filter((i) => !i.ended);
  const cls = s.classes.filter((c) => c.count);
  const maxc = Math.max(1, ...cls.map((c) => c.count));
  const worst = conts.filter((c) => c.total).sort((a, b) => b.offline / b.total - a.offline / a.total).slice(0, 8);
  if (S.page !== "today") return;
  $("#main").innerHTML = `
  <div class="grid">
    <div class="kpis">
      <div class="k click" data-go="asics" data-st="working"><div class="l">Работают</div><div class="v ok">${nf(agg.working)}<small>из ${nf(agg.total)}</small></div>
        <div class="bar"><span style="width:${pw(agg.working)}%;background:var(--ok)"></span><span style="width:${pw(agg.suspended)}%;background:var(--susp)"></span><span style="width:${pw(agg.offline)}%;background:var(--bad)"></span></div></div>
      <div class="k click" data-go="asics" data-st="offline"><div class="l">Офлайн</div><div class="v bad">${nf(agg.offline)}</div><div class="s">пауза ${nf(agg.suspended)}</div></div>
      <div class="k"><div class="l">Хешрейт</div><div class="v">${nf(agg.ths / 1000, 2)}<small>PH/s</small></div><div class="s">${nf(eff, 1)}% от паспорта</div></div>
      <div class="k"><div class="l">Мощность</div><div class="v">${nf(agg.kw / 1000, 2)}<small>МВт</small></div><div class="s">${nf(agg.kw * 24 / 1000, 1)} МВт·ч в сутки</div></div>
      <div class="k click" data-go="errors"><div class="l">С ошибками</div><div class="v warn">${nf(agg.al)}</div><div class="s">асиков</div></div>
      <div class="k"><div class="l">Аварии</div><div class="v ${openInc.length ? "bad" : "ok"}">${openInc.length}</div><div class="s">${openInc.length ? esc(openInc[0].scope) : "сейчас нет"}</div></div>
    </div>
    <div class="w c8"><div class="wh">Хешрейт фермы<span class="sp"></span><span class="seg" id="hrSeg"><button data-h="6">6 ч</button><button data-h="24" class="on">24 ч</button><button data-h="72">3 дня</button></span></div><div class="wb" id="hrChart"><div class="hint">Копим историю: точки появляются каждую минуту.</div></div></div>
    <div class="w c4"><div class="wh">Ошибки по классам<span class="sp"></span><a class="mini" href="#errors" data-go="errors">все</a></div><div class="wb"><div class="hl">
      ${cls.map((c) => `<div class="row" data-cls="${c.key}"><span class="nm">${esc(c.label)}</span><span class="num r">${nf(c.count)}</span><div class="tr"><span style="width:${c.count / maxc * 100}%;background:${c.key === "offline" ? "var(--bad)" : c.key === "temperature" ? "var(--avg)" : "var(--warn)"}"></span></div></div>`).join("") || '<div class="hint">Ошибок нет</div>'}
    </div></div></div>
    <div class="w c7"><div class="wh">Контейнеры: где больше офлайна<span class="sp"></span><a class="mini" href="#map" data-go="map">карта</a></div><div class="wb" style="padding:0"><table class="tbl"><thead><tr><th>Контейнер</th><th>Площадка</th><th class="r">Работают</th><th class="r">Офлайн</th><th class="r">TH/s</th><th class="r">кВт</th></tr></thead><tbody>
      ${worst.map((c) => `<tr><td class="lnk" data-cont="${c.id}">${esc(c.name)}</td><td>${esc((c.site || "").trim())}</td><td class="r num">${nf(c.working)}/${nf(c.total)}</td><td class="r num ${c.offline ? "bad" : ""}">${nf(c.offline)}</td><td class="r num">${nf(c.ths, 0)}</td><td class="r num">${nf(c.kw, 0)}</td></tr>`).join("")}
    </tbody></table></div></div>
    <div class="w c5"><div class="wh">Клиенты хостинга<span class="sp"></span><a class="mini" href="#clients" data-go="clients">все</a></div><div class="wb"><div class="hl">
      ${s.clients.slice(0, 8).map((c) => `<div class="row" data-client="${c.id || ""}"><span class="nm">${esc((c.name || "").trim())}</span><span class="num">${nf(c.working)}/${nf(c.total)}</span><div class="tr"><span style="width:${c.total ? c.working / c.total * 100 : 0}%"></span></div></div>`).join("")}
    </div></div></div>
  </div>`;
  bindCommon();
  $$("#hrSeg button").forEach((b) => b.onclick = () => { $$("#hrSeg button").forEach((x) => x.classList.toggle("on", x === b)); drawHr(+b.dataset.h); });
  drawHr(24);
}
async function drawHr(h) {
  const pts = await api("/farm/history?hours=" + h);
  const el = $("#hrChart"); if (!el) return;
  if (pts.length < 2) { el.innerHTML = '<div class="hint">Копим историю: точки появляются каждую минуту.</div>'; return; }
  el.innerHTML = lineChart(pts.map((p) => [new Date(p.at).getTime(), p.ths / 1000]), "PH/s");
}
function lineChart(pts, unit) {
  const W = 800, H = 170, P = { l: 44, r: 8, t: 8, b: 20 };
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs); let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.15 || 1; y0 = Math.max(0, y0 - pad); y1 += pad;
  const X = (x) => P.l + (x - x0) / (x1 - x0 || 1) * (W - P.l - P.r), Y = (y) => H - P.b - (y - y0) / (y1 - y0 || 1) * (H - P.t - P.b);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join("");
  const grid = [0, 0.5, 1].map((f) => { const v = y0 + (y1 - y0) * f; return `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l - 6}" y="${Y(v) + 3}" text-anchor="end">${nf(v, (y1 - y0) < 3 ? 2 : (y1 - y0) < 30 ? 1 : 0)}</text>`; }).join("");
  const tx = [x0, (x0 + x1) / 2, x1].map((t, i) => `<text x="${X(t)}" y="${H - 5}" text-anchor="${["start", "middle", "end"][i]}">${new Date(t).toLocaleString("ru-RU", Object.assign({ hour: "2-digit", minute: "2-digit" }, MSK))}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${grid}${tx}<path class="a" d="${d}L${X(x1)} ${H - P.b}L${X(x0)} ${H - P.b}Z"/><path class="l" d="${d}"/><text x="${W - P.r}" y="12" text-anchor="end">${unit}</text></svg>`;
}

const MODES = { normal: "обычный", low: "тихий", high: "разгон", sleep: "сон", eco: "эко", unknown: "—" };
function modeRu(m) { return m ? (MODES[String(m).toLowerCase()] || m) : "—"; }

// ---------- map ----------
let mapMode = "status";
async function pageMap() {
  await loadSummary();
  const devs = await loadDevices(true);
  const by = {};
  devs.forEach((d) => { (by[d.container_id || 0] = by[d.container_id || 0] || []).push(d); });
  const conts = filtSum().conts.filter((c) => by[c.id || 0]);
  const ipn = (ip) => (ip || "").split(".").reduce((a, x) => a * 256 + (+x || 0), 0);
  S.order = [];
  const box = conts.map((c) => {
    const list = by[c.id || 0].slice().sort((a, b) => ipn(a.ip) - ipn(b.ip) || String(a.num).localeCompare(String(b.num)));
    const w = list.filter((d) => d.status === "working").length, off = list.filter((d) => d.status === "offline").length;
    const ths = list.reduce((a, d) => a + (d.status === "working" ? d.ths || 0 : 0), 0);
    const tmax = Math.max(-99, ...list.map((d) => d.temp ?? -99));
    const cells = list.map((d) => { S.order.push(d.id); return `<i class="cell ${cellCls(d)}" data-id="${d.id}" title="${esc(cellTip(d))}"></i>`; }).join("");
    return `<div class="cbox"><h3><span title="${esc(c.name)}">${esc(c.name)}</span><button class="btn ghost" style="height:22px;padding:0 6px" data-selcont="${c.id}">выбрать</button></h3>
      <div class="meta"><span class="ok num">${w}</span>/<span class="num">${list.length}</span><span class="${off ? "bad" : ""}">офлайн ${off}</span><span class="num">${nf(ths, 0)} TH/s</span>${tmax > -99 ? `<span class="num ${tmax >= 75 ? "warn" : ""}">${nf(tmax, 0)}°</span>` : ""}</div>
      <div class="cells">${cells}</div></div>`;
  }).join("");
  if (S.page !== "map") return;
  $("#main").innerHTML = `<div class="fbar"><div class="legend"><span><i style="background:var(--ok)"></i>работает</span><span><i style="background:var(--bad)"></i>офлайн</span><span><i style="background:var(--susp)"></i>пауза</span><span><i style="background:var(--ok);box-shadow:inset 0 0 0 3px var(--ok),inset 0 0 0 6px var(--warn)"></i>есть ошибка</span></div>
    <span class="sp"></span><span class="hint">Клик — карточка · Ctrl/⌘ — добавить к выбору · Shift — диапазон</span>
    <span class="seg" id="mapMode"><button data-m="status" class="${mapMode === "status" ? "on" : ""}">Статус</button><button data-m="temp" class="${mapMode === "temp" ? "on" : ""}">Температура</button><button data-m="eff" class="${mapMode === "eff" ? "on" : ""}">% паспорта</button></span></div>
    <div class="cmap">${box || '<div class="empty">Нет устройств под фильтр</div>'}</div>`;
  if (mapMode !== "status") paintMode();
  $$("#mapMode button").forEach((b) => b.onclick = () => { mapMode = b.dataset.m; $$("#mapMode button").forEach((x) => x.classList.toggle("on", x === b)); pageMap(); });
  $(".cmap").addEventListener("click", (e) => {
    const sc = e.target.closest("[data-selcont]");
    if (sc) { by[+sc.dataset.selcont || 0].forEach((d) => S.sel.add(d.id)); markSel(); return; }
    const c = e.target.closest(".cell"); if (!c) return;
    pickId(+c.dataset.id, e) || openCard(+c.dataset.id);
  });
  markSel();
}
function cellCls(d) { return `${d.status}${d.alarms.length && d.status !== "offline" ? " alarm" : ""}${d.temp >= 80 ? " hot" : ""}`; }
function cellTip(d) { return `${d.model || ""} · ${d.ip || "без IP"}\n${ST[d.status] || d.status}${d.ths ? " · " + nf(d.ths, 1) + " TH/s" : ""}${d.temp != null ? " · " + nf(d.temp, 0) + "°" : ""}\n${d.worker || ""}${d.alarms.length ? "\n⚠ " + d.alarms.map((a) => a.msg).join("; ") : ""}`; }
function paintMode() {
  const m = new Map(S.devs.map((d) => [d.id, d]));
  $$(".cell").forEach((c) => {
    const d = m.get(+c.dataset.id); let col = "var(--off)";
    if (mapMode === "temp" && d.temp != null) { const t = d.temp; col = t >= 85 ? "var(--bad)" : t >= 75 ? "var(--avg)" : t >= 65 ? "var(--warn)" : t >= 45 ? "var(--ok)" : "var(--info)"; }
    if (mapMode === "eff" && d.status === "working" && d.nominal) { const p = d.ths / d.nominal; col = p >= 0.95 ? "var(--ok)" : p >= 0.85 ? "var(--warn)" : p >= 0.6 ? "var(--avg)" : "var(--bad)"; }
    c.className = "cell"; c.style.background = col;
  });
}

// ---------- selection ----------
function pickId(id, e) {
  if (e.shiftKey && S.lastIdx != null) {
    const a = S.order.indexOf(S.lastIdx), b = S.order.indexOf(id);
    if (a >= 0 && b >= 0) { const [i, j] = a < b ? [a, b] : [b, a]; S.order.slice(i, j + 1).forEach((x) => S.sel.add(x)); markSel(); return true; }
  }
  if (e.ctrlKey || e.metaKey || S.sel.size) { S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); S.lastIdx = id; markSel(); return true; }
  S.lastIdx = id; return false;
}
function markSel() {
  $$(".cell").forEach((c) => c.classList.toggle("on", S.sel.has(+c.dataset.id)));
  $$("tr[data-id]").forEach((r) => { const on = S.sel.has(+r.dataset.id); r.classList.toggle("on", on); const cb = $("input", r); if (cb) cb.checked = on; });
  updSel();
}
function updSel() { $("#selbar").hidden = !S.sel.size; $("#selN").textContent = S.sel.size; }
$("#selbar").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act]"); if (!a) return;
  const ids = [...S.sel];
  if (a.dataset.act === "clear") { S.sel.clear(); markSel(); }
  if (a.dataset.act === "export") { download("/farm/errors/export.xlsx?ids=" + ids.join(",")); }
  if (a.dataset.act === "copy") { const m = new Map((S.devs || []).map((d) => [d.id, d])); const t = ids.map((i) => (m.get(i) || {}).ip).filter(Boolean).join("\n"); navigator.clipboard.writeText(t).then(() => toast(`Скопировано IP: ${t.split("\n").length}`)); }
  if (a.dataset.act === "compare") compare(ids);
});
async function compare(ids) {
  const devs = await loadDevices(); const m = new Map(devs.map((d) => [d.id, d]));
  const list = ids.map((i) => m.get(i)).filter(Boolean).slice(0, 500);
  const avg = (f) => { const v = list.map(f).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  $("#mdIn").innerHTML = `<div class="dr-h"><h2>Сравнение · ${list.length} асиков</h2><span class="sp"></span><button class="btn" id="mdX">Закрыть</button></div>
   <div class="fbar"><span class="pill">средний хешрейт <b class="num">${nf(avg((d) => d.status === "working" ? d.ths : null), 1)} TH/s</b></span><span class="pill">средняя t° <b class="num">${nf(avg((d) => d.temp), 1)}</b></span><span class="pill">работают <b class="num">${list.filter((d) => d.status === "working").length}</b></span><span class="hint">Пакетная смена пула, воркера и режима появится, когда в CROFT будет учётка с правом управления.</span></div>
   <div class="scroll"><table class="tbl"><thead><tr><th>Модель</th><th>IP</th><th>Статус</th><th class="r">TH/s</th><th class="r">% паспорта</th><th class="r">t°</th><th class="r">Вт</th><th>Режим</th><th>Пул</th><th>Воркер</th><th>Ошибки</th></tr></thead><tbody>
   ${list.map((d) => `<tr data-id="${d.id}"><td class="lnk" data-card="${d.id}">${esc(d.model)}</td><td class="num">${esc(d.ip || "—")}</td><td>${stPill(d.status)}</td><td class="r num">${nf(d.ths, 1)}</td><td class="r num">${d.nominal && d.ths ? nf(d.ths / d.nominal * 100, 0) + "%" : "—"}</td><td class="r num">${nf(d.temp, 0)}</td><td class="r num">${nf(d.w, 0)}</td><td>${esc(d.mode || "")}</td><td>${esc(shortPool(d.pool))}</td><td>${esc(d.worker || "")}</td><td>${d.alarms.map((a) => `<span class="lv${a.level}">${esc(a.msg)}</span>`).join(", ")}</td></tr>`).join("")}
   </tbody></table></div>`;
  $("#modal").hidden = false; $("#mdX").onclick = () => ($("#modal").hidden = true);
  $$("[data-card]", $("#mdIn")).forEach((x) => x.onclick = () => { $("#modal").hidden = true; openCard(+x.dataset.card); });
}

// ---------- asics ----------
async function pageAsics() {
  await loadSummary();
  const devs = await loadDevices(true);
  const f = S.f; const q = f.q.trim().toLowerCase();
  let list = devs.filter((d) => (!f.status || d.status === f.status) && (!f.container || String(d.container_id) === f.container) && (!f.model || d.model === f.model) && (!f.alarms || d.alarms.length) && (!f.cls || d.alarms.some((a) => a.cls === f.cls))
    && (!q || [d.ip, d.mac, d.serial, d.worker, d.num, d.model, d.container].some((x) => x && String(x).toLowerCase().includes(q))));
  const key = f.sort.replace("-", ""), desc = f.sort[0] === "-";
  const ipn = (ip) => (ip || "").split(".").reduce((a, x) => a * 256 + (+x || 0), 0);
  list.sort((a, b) => { let x = a[key], y = b[key]; if (key === "ip") { x = ipn(x); y = ipn(y); } if (key === "alarms") { x = a.alarms.length; y = b.alarms.length; } if (x == null) return 1; if (y == null) return -1; return (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "ru")) * (desc ? -1 : 1); });
  S.order = list.map((d) => d.id); S.view = list;
  const cnt = (fn) => devs.filter(fn).length;
  const models = [...new Set(devs.map((d) => d.model).filter(Boolean))].sort();
  const conts = filtSum().conts;
  const show = list.slice(0, 1500);
  const th = (k, t, r) => `<th class="${r ? "r" : ""}" data-sort="${k}">${t}${key === k ? (desc ? " ↓" : " ↑") : ""}</th>`;
  if (S.page !== "asics") return;
  $("#main").innerHTML = `
   <div class="fbar"><input type="search" id="q" placeholder="IP, MAC, серийник, воркер, номер…" value="${esc(f.q)}">
    <div class="chips">${[["", "Все", devs.length], ["working", "Работают", cnt((d) => d.status === "working")], ["offline", "Офлайн", cnt((d) => d.status === "offline")], ["suspended", "Пауза", cnt((d) => d.status === "suspended")]].map(([v, t, n]) => `<button class="chip ${f.status === v ? "on" : ""}" data-st="${v}">${t} <b>${nf(n)}</b></button>`).join("")}
     <button class="chip ${f.alarms ? "on" : ""}" id="onlyAl">С ошибками <b>${nf(cnt((d) => d.alarms.length))}</b></button></div>
    <select class="sel" id="fCont"><option value="">Все контейнеры</option>${conts.map((c) => `<option value="${c.id}" ${String(c.id) === f.container ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
    <select class="sel" id="fModel"><option value="">Все модели</option>${models.map((m) => `<option ${m === f.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
    <span class="sp"></span><span class="hint">${nf(list.length)} в выборке</span>
    <button class="btn" id="selAll">Выделить отфильтрованные</button><button class="btn" id="csv">CSV</button></div>
   <div class="w"><div class="scroll"><table class="tbl" id="tA"><thead><tr><th class="cb"><input type="checkbox" id="cbAll" aria-label="Выделить всё"></th>${th("model", "Модель")}${th("ip", "IP")}${th("status", "Статус")}${th("ths", "TH/s", 1)}${th("temp", "t°", 1)}${th("w", "Вт", 1)}${th("container", "Контейнер")}${th("client", "Клиент")}${th("worker", "Воркер")}${th("uptime", "Аптайм", 1)}${th("alarms", "Ошибки")}</tr></thead><tbody>
   ${show.map((d) => `<tr data-id="${d.id}"><td class="cb"><input type="checkbox" aria-label="выбрать"></td><td class="lnk" data-card="${d.id}">${esc(d.model)}</td><td class="num">${esc(d.ip || "—")}</td><td>${stPill(d.status)}</td><td class="r num">${d.status === "working" ? nf(d.ths, 1) : "—"}</td><td class="r num ${d.temp >= 80 ? "warn" : ""}">${nf(d.temp, 0)}</td><td class="r num">${nf(d.w, 0)}</td><td title="${esc(d.container)}">${esc(d.container || "—")}</td><td>${esc(d.client || "—")}</td><td title="${esc(d.worker)}">${esc(d.worker || "—")}</td><td class="r num">${d.status === "working" ? dur(d.uptime) : "—"}</td><td>${d.alarms.map((a) => `<span class="lv${a.level}" title="${esc(a.code)}">${esc(a.msg)}</span>`).join(", ")}</td></tr>`).join("")}
   </tbody></table>${list.length > show.length ? `<div class="empty">Показаны первые ${nf(show.length)} из ${nf(list.length)} — уточните фильтр или выгрузите CSV.</div>` : ""}${!list.length ? '<div class="empty">Ничего не найдено</div>' : ""}</div></div>`;
  let qt; $("#q").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { f.q = e.target.value; pageAsics().then(() => { const i = $("#q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250); };
  $$(".chip[data-st]").forEach((c) => c.onclick = () => { f.status = c.dataset.st; pageAsics(); });
  $("#onlyAl").onclick = () => { f.alarms = !f.alarms; pageAsics(); };
  $("#fCont").onchange = (e) => { f.container = e.target.value; pageAsics(); };
  $("#fModel").onchange = (e) => { f.model = e.target.value; pageAsics(); };
  $$("th[data-sort]").forEach((h) => h.onclick = () => { const k = h.dataset.sort; f.sort = f.sort === k ? "-" + k : k; pageAsics(); });
  $("#selAll").onclick = () => { list.forEach((d) => S.sel.add(d.id)); markSel(); };
  $("#cbAll").onchange = (e) => { show.forEach((d) => e.target.checked ? S.sel.add(d.id) : S.sel.delete(d.id)); markSel(); };
  $("#csv").onclick = () => csv(list);
  tableClicks("#tA");
  markSel();
}
function tableClicks(sel) {
  $(sel).addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-id]"); if (!tr) return; const id = +tr.dataset.id;
    if (e.target.matches("input[type=checkbox]")) { e.target.checked ? S.sel.add(id) : S.sel.delete(id); S.lastIdx = id; updSel(); tr.classList.toggle("on", e.target.checked); return; }
    if (e.shiftKey || e.ctrlKey || e.metaKey) { pickId(id, e); window.getSelection().removeAllRanges(); return; }
    openCard(id);
  });
}
const stPill = (s) => `<span class="pill st-${s}"><i></i>${ST[s] || s}</span>`;
const shortPool = (u) => (u || "").replace(/^stratum\+tcp:\/\//, "");
function csv(list) {
  const head = ["Модель", "IP", "MAC", "Серийный", "Статус", "TH/s", "t", "Вт", "Площадка", "Контейнер", "Клиент", "Воркер", "Пул", "Ошибки"];
  const rows = list.map((d) => [d.model, d.ip, d.mac, d.serial, ST[d.status], d.ths, d.temp, d.w, d.site, d.container, d.client, d.worker, d.pool, d.alarms.map((a) => a.msg).join("; ")]);
  const t = "\ufeff" + [head, ...rows].map((r) => r.map((x) => `"${String(x == null ? "" : x).replace(/"/g, '""')}"`).join(";")).join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([t], { type: "text/csv" })); a.download = "kirka_asics.csv"; a.click();
}

// ---------- errors ----------
async function pageErrors() {
  await loadSummary();
  const ef = S.ef;
  const r = await api("/farm/errors?" + qs({ site: site(), client: client(), cls: ef.cls, container: ef.container, model: ef.model, group: ef.group, q: ef.q, level: ef.level }));
  S.errItems = r.items;
  const conts = filtSum().conts;
  const models = [...new Set(S.sum.containers.length ? r.items.map((x) => x.model).filter(Boolean) : [])].sort();
  const all = S.sum.classes;
  const maxg = Math.max(1, ...r.groups.map((g) => g.count));
  const show = r.items.slice(0, 1500);
  S.order = [...new Set(show.map((x) => x.id))];
  if (S.page !== "errors") return;
  $("#main").innerHTML = `
  <div class="fbar"><input type="search" id="eq" placeholder="IP, воркер, серийник…" value="${esc(ef.q)}">
   <div class="chips"><button class="chip ${!ef.cls ? "on" : ""}" data-cls="">Все классы</button>${all.filter((c) => c.count).map((c) => `<button class="chip ${ef.cls === c.key ? "on" : ""}" data-cls="${c.key}">${esc(c.label)} <b>${nf(c.count)}</b></button>`).join("")}</div>
   <select class="sel" id="eCont"><option value="">Все контейнеры</option>${conts.map((c) => `<option value="${c.id}" ${String(c.id) === ef.container ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
   <select class="sel" id="eModel"><option value="">Все модели</option>${models.map((m) => `<option ${m === ef.model ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
   <select class="sel" id="eLvl"><option value="">Любой уровень</option><option value="1" ${ef.level === "1" ? "selected" : ""}>от предупреждения</option><option value="2" ${ef.level === "2" ? "selected" : ""}>от «опасно»</option><option value="3" ${ef.level === "3" ? "selected" : ""}>только критичные</option></select>
   <span class="sp"></span><button class="btn" id="eSelAll">Выделить все ${nf(r.devices)}</button><button class="btn pri" id="eXls">Выгрузить Excel</button></div>
  <div class="grid">
   <div class="w c4"><div class="wh">Группировать<span class="sp"></span><span class="seg" id="eGrp">${[["code", "ошибка"], ["cls", "класс"], ["container", "контейнер"], ["client", "клиент"], ["model", "модель"]].map(([k, t]) => `<button data-g="${k}" class="${ef.group === k ? "on" : ""}">${t}</button>`).join("")}</span></div>
    <div class="wb"><div class="hint" style="margin-bottom:8px">${nf(r.total)} ошибок на ${nf(r.devices)} асиках. Клик по группе — фильтр.</div><div class="hl">
    ${r.groups.slice(0, 40).map((g) => `<div class="row" data-gk="${esc(g.key)}"><span class="nm" title="${esc(g.key)}">${esc(g.key)}</span><span class="num">${nf(g.devices)}</span><div class="tr"><span style="width:${g.count / maxg * 100}%"></span></div></div>`).join("") || '<div class="hint">Ошибок нет</div>'}</div></div></div>
   <div class="w c8"><div class="scroll"><table class="tbl" id="tE"><thead><tr><th class="cb"><input type="checkbox" id="ecbAll" aria-label="Выделить все"></th><th>Ошибка</th><th>Класс</th><th>Уровень</th><th>С какого времени</th><th>Модель</th><th>IP</th><th>Контейнер</th><th>Клиент</th><th class="r">t°</th></tr></thead><tbody>
    ${show.map((x) => `<tr data-id="${x.id}" data-gk="${esc(x.code + " · " + x.msg)}"><td class="cb"><input type="checkbox" aria-label="выбрать"></td><td><span class="lv${x.lvl}">${esc(x.msg)}</span> <span class="faint num">${esc(x.code)}</span></td><td>${esc(x.cls_label)}</td><td class="lv${x.lvl}">${LV[x.lvl] || "—"}</td><td class="num">${tfmt(x.since)}</td><td>${esc(x.model || "")}</td><td class="num">${esc(x.ip || "—")}</td><td title="${esc(x.container)}">${esc(x.container || "—")}</td><td>${esc(x.client || "—")}</td><td class="r num">${nf(x.temp, 0)}</td></tr>`).join("")}
    </tbody></table>${r.items.length > show.length ? `<div class="empty">Показаны первые ${nf(show.length)} из ${nf(r.items.length)}. Полный список — в Excel.</div>` : ""}</div></div>
  </div>`;
  let qt; $("#eq").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { ef.q = e.target.value; pageErrors().then(() => { const i = $("#eq"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  $$(".chip[data-cls]").forEach((c) => c.onclick = () => { ef.cls = c.dataset.cls; pageErrors(); });
  $("#eCont").onchange = (e) => { ef.container = e.target.value; pageErrors(); };
  $("#eModel").onchange = (e) => { ef.model = e.target.value; pageErrors(); };
  $("#eLvl").onchange = (e) => { ef.level = e.target.value; pageErrors(); };
  $$("#eGrp button").forEach((b) => b.onclick = () => { ef.group = b.dataset.g; pageErrors(); });
  $$(".hl .row[data-gk]").forEach((row) => row.onclick = () => {
    const k = row.dataset.gk;
    if (ef.group === "container") { const c = conts.find((x) => x.name === k); ef.container = c ? String(c.id) : ""; return pageErrors(); }
    if (ef.group === "model") { ef.model = k; return pageErrors(); }
    if (ef.group === "cls") { const c = all.find((x) => x.label === k); ef.cls = c ? c.key : ""; return pageErrors(); }
    if (ef.group === "client") { const c = S.sum.clients.find((x) => (x.name || "").trim() === k.trim()); if (c && c.id) { $("#clientSel").value = c.id; } return pageErrors(); }
    const ids = r.items.filter((x) => `${x.code} · ${x.msg}` === k).map((x) => x.id); ids.forEach((i) => S.sel.add(i)); markSel(); toast(`Выделено асиков: ${new Set(ids).size}`);
  });
  $("#eSelAll").onclick = () => { r.items.forEach((x) => S.sel.add(x.id)); markSel(); };
  $("#ecbAll").onchange = (e) => { show.forEach((x) => e.target.checked ? S.sel.add(x.id) : S.sel.delete(x.id)); markSel(); };
  $("#eXls").onclick = () => download("/farm/errors/export.xlsx?" + qs({ site: site(), client: client(), cls: ef.cls, container: ef.container, model: ef.model, ids: S.sel.size ? [...S.sel].join(",") : "" }));
  tableClicks("#tE");
  markSel();
}

// ---------- clients ----------
async function pageClients() {
  await loadSummary();
  const cl = S.sum.clients;
  if (S.page !== "clients") return;
  $("#main").innerHTML = `<div class="w"><div class="wh">Клиенты хостинга · ${cl.length}<span class="sp"></span><span class="mini">Вид клиента — как в CROFT «человечек»: только его асики</span></div><div class="scroll"><table class="tbl"><thead><tr><th>Клиент</th><th class="r">Асиков</th><th class="r">Работают</th><th class="r">Офлайн</th><th class="r">Хешрейт, TH/s</th><th class="r">Доля работающих</th></tr></thead><tbody>
   ${cl.map((c) => `<tr><td class="lnk" data-client="${c.id || ""}">${esc((c.name || "").trim())}</td><td class="r num">${nf(c.total)}</td><td class="r num ok">${nf(c.working)}</td><td class="r num ${c.offline ? "bad" : ""}">${nf(c.offline)}</td><td class="r num">${nf(c.ths, 0)}</td><td class="r num">${c.total ? nf(c.working / c.total * 100, 0) + "%" : "—"}</td></tr>`).join("")}
   </tbody></table></div></div>`;
  bindCommon();
}

// ---------- ASIC card ----------
async function openCard(id) {
  $("#drIn").innerHTML = '<div class="spin" style="margin:40px auto"></div>'; $("#drawer").hidden = false;
  const d = await api("/farm/devices/" + id);
  const evName = { seen: "Впервые в Kirka", status: "Статус", alarm_on: "Ошибка", alarm_off: "Ошибка ушла", worker: "Воркер", pool: "Пул", ip: "IP", container: "Перемещение", client: "Клиент" };
  const evCol = { alarm_on: "var(--bad)", alarm_off: "var(--ok)", status: "var(--info)", worker: "var(--acc)", pool: "var(--acc)", ip: "var(--acc)", container: "var(--warn)", client: "var(--warn)", seen: "var(--mut)" };
  const evTxt = (e) => e.kind === "status" ? `${ST[e.old] || e.old} → ${ST[e.new] || e.new}` : e.kind.startsWith("alarm") ? `${esc(e.new || e.old)} <span class="faint">${esc(e.code)} · ${CLS[e.cls] || ""}</span>` : `${esc(e.old || "—")} → ${esc(e.new || "—")}`;
  const ups = d.hourly.length ? d.hourly.reduce((a, h) => a + h.up, 0) / d.hourly.length : null;
  $("#drIn").innerHTML = `
   <div class="dr-h"><div><h2>${esc(d.model)} ${d.num ? `<span class="faint">№ ${esc(d.num)}</span>` : ""}</h2><div class="mut small">${esc((d.site || "").trim())} · ${esc(d.container || "")}</div></div><span class="sp"></span>${stPill(d.status)}<button class="btn" id="drX">Закрыть</button></div>
   <div class="life">
    <div class="k"><div class="l">Хешрейт</div><div class="v">${d.status === "working" ? nf(d.ths, 1) : "—"}<small>TH/s</small></div><div class="s">паспорт ${nf(d.nominal, 0)}${d.nominal && d.ths ? " · " + nf(d.ths / d.nominal * 100, 0) + "%" : ""}</div></div>
    <div class="k"><div class="l">Без перезагрузки</div><div class="v">${d.status === "working" ? dur(d.uptime) : "—"}</div><div class="s">${d.status === "working" ? "сейчас в работе" : (ST[d.status] || "") + " с " + tfmt(d.since)}</div></div>
    <div class="k"><div class="l">Онлайн за 72 ч</div><div class="v">${ups == null ? "—" : nf(ups, 0) + "%"}</div><div class="s">в Kirka с ${tfmt(d.first_seen)}</div></div>
   </div>
   <div class="sec"><h4>Паспорт</h4><dl class="kv">
    <dt>IP</dt><dd class="num">${esc(d.ip || "—")}</dd><dt>MAC</dt><dd class="num">${esc(d.mac || "—")}</dd><dt>Серийный номер</dt><dd class="num">${esc(d.serial || "—")}</dd>
    <dt>Клиент</dt><dd>${esc(d.client || "—")}</dd><dt>Режим</dt><dd>${esc(modeRu(d.mode))}</dd><dt>Мощность</dt><dd class="num">${nf(d.w, 0)} Вт</dd><dt>Температура</dt><dd class="num">${nf(d.temp, 0)}°</dd>
    <dt>Теги</dt><dd>${d.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join(" ") || "—"}</dd></dl></div>
   <div class="sec"><h4>Воркер и пулы</h4><dl class="kv"><dt>Воркер</dt><dd class="num">${esc(d.worker || "—")}</dd>${d.pools.filter((p) => p.url).map((p, i) => `<dt>Пул ${i + 1}</dt><dd class="num"><span class="${p.alive ? "ok" : "bad"}">●</span> ${esc(shortPool(p.url))}</dd>`).join("")}</dl></div>
   <div class="sec"><h4>Ошибки сейчас</h4>${d.alarms.length ? `<ul class="tl">${d.alarms.map((a) => `<li><span class="d">${tfmt(a.since)}</span><span class="dot" style="background:var(--bad)"></span><span><span class="lv${a.level}">${esc(a.msg)}</span> <span class="faint">${esc(a.code)} · ${CLS[a.cls] || ""} · ${LV[a.level] || ""}</span></span></li>`).join("")}</ul>` : '<div class="hint">Ошибок нет</div>'}</div>
   <div class="sec"><h4>Хешрейт по часам, 72 ч</h4>${d.hourly.length > 1 ? lineChart(d.hourly.map((h) => [new Date(h.h).getTime(), h.ths]), "TH/s") : '<div class="hint">История копится с момента подключения Kirka.</div>'}</div>
   <div class="sec"><h4>История болезни</h4>${d.events.length ? `<ul class="tl">${d.events.map((e) => `<li><span class="d">${tfmt(e.at)}</span><span class="dot" style="background:${evCol[e.kind] || "var(--mut)"}"></span><span><b>${evName[e.kind] || e.kind}</b> · ${evTxt(e)}</span></li>`).join("")}</ul>` : '<div class="hint">Пока событий нет</div>'}</div>`;
  $("#drX").onclick = () => ($("#drawer").hidden = true);
}
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("#drawer").hidden = true; });
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#drawer").hidden = true; $("#modal").hidden = true; } if ((e.ctrlKey || e.metaKey) && e.key === "a" && ["map", "asics", "errors"].includes(S.page) && !e.target.matches("input")) { e.preventDefault(); S.order.forEach((i) => S.sel.add(i)); markSel(); } });

// ---------- common ----------
function bindCommon() {
  $$("[data-go]").forEach((x) => x.onclick = (e) => { e.preventDefault(); if (x.dataset.st != null) { S.f.status = x.dataset.st; } go(x.dataset.go); });
  $$("[data-cls]", $(".hl") || document).forEach((x) => { if (x.classList.contains("row")) x.onclick = () => { S.ef.cls = x.dataset.cls; go("errors"); }; });
  $$("[data-cont]").forEach((x) => x.onclick = () => { S.f.container = x.dataset.cont; S.f.status = ""; go("asics"); });
  $$("[data-client]").forEach((x) => x.onclick = () => { $("#clientSel").value = x.dataset.client; S.devs = null; go("asics"); });
}
async function download(path) {
  const t = tok.get(); const r = await fetch(API + path, { headers: { Authorization: "Bearer " + (t && t.access_token) } });
  if (!r.ok) return toast("Не удалось выгрузить");
  const b = await r.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(b);
  a.download = (r.headers.get("Content-Disposition") || "").split('filename="')[1]?.replace(/"/g, "") || "kirka.xlsx"; a.click();
}
function toast(t) { const el = $("#toast"); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (el.hidden = true), 2200); }

$$(".nav[data-page]").forEach((n) => n.onclick = () => go(n.dataset.page));
$("#siteSel").onchange = $("#clientSel").onchange = () => { S.devs = null; go(S.page, true); };
$("#themeBtn").onclick = () => { const h = document.documentElement; h.dataset.theme = h.dataset.theme === "dark" ? "light" : "dark"; localStorage.setItem("kirka_theme", h.dataset.theme); };
$("#logoutBtn").onclick = () => { tok.clear(); showLogin(); };
document.documentElement.dataset.theme = localStorage.getItem("kirka_theme") || "dark";

let tick = null;
async function boot() {
  if (!tok.get()) return showLogin();
  $("#login").hidden = true; $("#app").hidden = false;
  try { const me = await api("/auth/me"); $("#av").textContent = ((me.full_name || me.email || "K")[0] || "K").toUpperCase(); } catch (e) { if (e.message === "auth") return showLogin(); }
  const p = (location.hash || "#today").slice(1);
  await go(PAGES[p] ? p : "today");
  clearInterval(tick);
  tick = setInterval(() => { if (document.hidden || !$("#drawer").hidden || !$("#modal").hidden || S.sel.size) return; S.devs = null; go(S.page, true); }, 60000);
}
boot();
