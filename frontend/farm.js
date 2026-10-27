"use strict";
// Kirka farm panel — vanilla JS over /api/v1/farm/*. Data: CROFT (read-only), refresh 60 s.
const API = "/api/v1";
const TOKENS = "kirkalab_tokens";
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NF = {};
const nf = (x, d = 0) => (x == null || isNaN(x) ? "—" : (NF[d] || (NF[d] = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: d, maximumFractionDigits: d }))).format(Number(x)));
const MSK = { timeZone: "Europe/Moscow" };
const TF = {};
const tfmt = (iso, withDate = true) => { if (!iso) return "—"; const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso); const k = withDate ? 1 : 0; return (TF[k] || (TF[k] = new Intl.DateTimeFormat("ru-RU", Object.assign({ hour: "2-digit", minute: "2-digit" }, withDate ? { day: "2-digit", month: "2-digit" } : {}, MSK)))).format(d); };
const dur = (sec) => { if (sec == null) return "—"; sec = Math.max(0, Math.floor(sec)); const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60); return d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`; };
const ST = { working: "Работает", offline: "Офлайн", suspended: "Сон", unknown: "Нет данных" };
const LV = { 0: "Инфо", 1: "Предупреждение", 2: "Опасно", 3: "Критично" };
const CLS = { offline: "Нет связи", power: "Питание", temperature: "Температура", hashrate: "Хешрейт", hardware: "Аппаратные", network: "Сеть и пул", config: "Конфигурация", pressure: "Давление гидро", other: "Прочее" };

// ---------- auth ----------
const tok = { get() { try { return JSON.parse(localStorage.getItem(TOKENS)); } catch (e) { return null; } }, set(t) { localStorage.setItem(TOKENS, JSON.stringify(t)); }, clear() { localStorage.removeItem(TOKENS); } };
async function api(path, opts = {}, retry) {
  if (opts.method && opts.method !== "GET" && typeof S !== "undefined") { S.sumAt = 0; S.devAt = 0; }
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
const S = { page: "today", arg: null, sum: null, devs: null, devAt: 0, lay: null, layAt: 0, sel: new Set(), lastIdx: null, order: [],
  f: { q: "", status: "", cls: "", container: "", model: "", alarms: false, quick: "", ids: null, sort: "place" },
  ef: { cls: "", container: "", model: "", group: "code", q: "", level: "", gk: "" },
  mf: { kind: "", sort: "problems", layer: "status" }, cl: { layer: "status", selMode: false } };
// Мультивыбор площадок и клиентов: пустое множество = «все». Выбор помнится между заходами.
const MS = { site: new Set(JSON.parse(localStorage.getItem("kirka_ms_site") || "[]")), client: new Set(JSON.parse(localStorage.getItem("kirka_ms_client") || "[]")) };
const msSave = () => { localStorage.setItem("kirka_ms_site", JSON.stringify([...MS.site])); localStorage.setItem("kirka_ms_client", JSON.stringify([...MS.client])); };
const site = () => [...MS.site].join(","), client = () => [...MS.client].join(",");
const setClient = (id) => { MS.client = new Set(id ? [String(id)] : []); msSave(); };
const qs = (o) => Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== false).map(([k, v]) => k + "=" + encodeURIComponent(v)).join("&");
const KIND = { air: "Воздух", hydro: "Гидро", bath: "Ванны" };

async function loadSummary(force) {
  // Сводку берём из кэша, если ей меньше 20 с: клик по фильтру не ждёт сеть. Таймер обновления сбрасывает кэш.
  if (force || !S.sum || !S.sumAt || Date.now() - S.sumAt >= 20000) { S.sum = await api("/farm/summary"); S.sumAt = Date.now(); }
  const s = S.sum;
  if (S.all) scope(); else msRender({}, {}, 0, 0);
  const age = s.synced_at ? (Date.now() - new Date(s.synced_at)) / 1000 : 1e9;
  $("#syncAt").textContent = s.synced_at ? "обновлено " + tfmt(s.synced_at, false) : "нет данных";
  $(".auto").classList.toggle("stale", age > 300);
  const b = $("#errBadge"); b.hidden = !s.with_alarms; b.textContent = nf(s.with_alarms);
}
// Все асики грузим один раз, площадку и клиента фильтруем на месте: так списки
// площадок и клиентов показывают только реальные варианты с количеством.
async function loadDevices(force) {
  if (force || !S.all || Date.now() - S.devAt >= 55000) {
    const r = await api("/farm/devices?limit=6000");
    S.all = r.items; S.devAt = Date.now();
  }
  scope();
  return S.devs;
}
const inSite = (d) => !MS.site.size || MS.site.has(String(d.site_id));
const inClient = (d) => !MS.client.size || MS.client.has(String(d.client_id));
function scope() {
  const cntBy = (arr, k) => arr.reduce((o, d) => { if (d[k]) o[d[k]] = (o[d[k]] || 0) + 1; return o; }, {});
  const sC0 = cntBy(S.all, "site_id");
  [...MS.site].forEach((x) => { if (!sC0[x]) MS.site.delete(x); });
  const cC = cntBy(S.all.filter(inSite), "client_id");
  [...MS.client].forEach((x) => { if (!cC[x]) MS.client.delete(x); });
  const sC = cntBy(S.all.filter(inClient), "site_id");
  msSave();
  msRender(sC, cC, S.all.filter(inClient).length, S.all.filter(inSite).length);
  S.devs = S.all.filter((d) => inSite(d) && inClient(d));
  S.devMap = new Map(S.all.map((d) => [d.id, d]));
}
// Кнопка-выпадашка с галочками. Пункты с нулём асиков не показываем.
function msRender(sC, cC, nAll, nCl) {
  const sites = (S.sum ? S.sum.sites : []).map((x) => ({ id: String(x.id), name: x.name.trim(), n: sC[x.id] || 0 })).filter((x) => x.n || MS.site.has(x.id));
  const cls = (S.sum ? S.sum.clients : []).filter((c) => c.id).map((c) => ({ id: String(c.id), name: c.name.trim(), n: cC[c.id] || 0 })).filter((x) => x.n || MS.client.has(x.id)).sort((a, b) => b.n - a.n || a.name.localeCompare(b.name, "ru"));
  msBox($("#siteSel"), "site", sites, "Все площадки", nAll, ["площадка", "площадки", "площадок"]);
  msBox($("#clientSel"), "client", cls, "Все клиенты", nCl, ["клиент", "клиента", "клиентов"]);
}
const plural = (n, f) => { const a = Math.abs(n) % 100, b = a % 10; return f[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2]; };
function msBox(el, key, items, allTxt, nAll, forms) {
  if (!el) return;
  const set = MS[key], open = el.classList.contains("open"), q = (($(".msq", el) || {}).value || "");
  const picked = items.filter((x) => set.has(x.id));
  const label = !picked.length ? `${allTxt} · ${nf(nAll)}` : picked.length === 1 ? `${esc(picked[0].name)} · ${nf(picked[0].n)}` : `${picked.length} ${plural(picked.length, forms)} · ${nf(picked.reduce((a, x) => a + x.n, 0))}`;
  el.innerHTML = `<button class="sel msb ${picked.length ? "on" : ""}" type="button">${label}</button>
   <div class="msp" ${open ? "" : "hidden"}>${items.length > 8 ? `<input class="msq" placeholder="Найти…" value="${esc(q)}">` : ""}
    <label class="msi msall"><input type="checkbox" ${picked.length ? "" : "checked"} data-all> <span>${allTxt}</span><b>${nf(nAll)}</b></label>
    <div class="msl">${items.map((x) => `<label class="msi" data-n="${esc(x.name.toLowerCase())}"><input type="checkbox" value="${x.id}" ${set.has(x.id) ? "checked" : ""}> <span>${esc(x.name)}</span><b>${nf(x.n)}</b></label>`).join("")}</div>
    <div class="msf"><button class="btn sm" data-only>Только отмеченные</button><button class="btn sm ghost" data-clear>Сбросить</button></div></div>`;
  const apply = () => { S.devs = null; msSave(); go(S.page, true, S.arg); };
  const flt = () => { const v = (($(".msq", el) || {}).value || "").toLowerCase(); $$(".msi[data-n]", el).forEach((r) => r.hidden = v && !r.dataset.n.includes(v)); };
  flt();
  $(".msb", el).onclick = (e) => { e.stopPropagation(); $$(".msel.open").forEach((o) => o !== el && (o.classList.remove("open"), $(".msp", o).hidden = true)); el.classList.toggle("open"); $(".msp", el).hidden = !el.classList.contains("open"); if (el.classList.contains("open") && $(".msq", el)) $(".msq", el).focus(); };
  if ($(".msq", el)) $(".msq", el).oninput = flt;
  $(".msp", el).onclick = (e) => e.stopPropagation();
  $$(".msl input", el).forEach((c) => c.onchange = () => { c.checked ? set.add(c.value) : set.delete(c.value); apply(); });
  $("[data-all]", el).onchange = $("[data-clear]", el).onclick = () => { set.clear(); apply(); };
  $("[data-only]", el).onclick = () => { el.classList.remove("open"); apply(); };
}
document.addEventListener("click", () => $$(".msel.open").forEach((o) => { o.classList.remove("open"); const p = $(".msp", o); if (p) p.hidden = true; }));
async function loadLayouts(force) {
  if (!force && S.lay && Date.now() - S.layAt < 120000) return S.lay;
  const r = await api("/farm/layouts");
  S.lay = {}; r.items.forEach((x) => (S.lay[x.id] = x)); S.layAt = Date.now();
  return S.lay;
}
function placeOf(d) {
  const L = S.lay && S.lay[d.container_id]; if (!L) return null;
  const p = L.placed[d.id]; if (!p) return null;
  const sec = L.layout.sections[p[0]];
  return { sec, si: p[0], rack: (sec.rack_from || 1) + p[1], shelf: p[2] + 1, slot: p[3] + 1, num: p[4], kind: L.layout.kind };
}
function placeTxt(d, short) {
  const p = placeOf(d); if (!p) return short ? "—" : "место не определено";
  if (p.kind === "bath") return `№ ${p.num}`;
  if (p.kind === "hydro") return `${short ? p.sec.title.replace("Кабинет ", "каб. ").replace(/ · подсеть.*/, "") : p.sec.title} · полка ${p.shelf} · место ${p.num}`;
  return `стеллаж ${p.rack} · полка ${p.shelf} · место ${p.num}`;
}

// ---------- routing ----------
const PAGES = { today: pageToday, map: pageMap, cont: pageCont, asics: pageAsics, errors: pageErrors, clients: pageClients, pools: pagePools, calc: pageCalc, notify: pageNotify, energy: pageEnergy };
const TITLES = { today: "Обзор фермы", map: "Контейнеры", cont: "Контейнер", asics: "Асики", errors: "Проблемы", clients: "Клиенты хостинга", pools: "Пулы", calc: "Калькулятор доходности", notify: "Уведомления", energy: "Энергетика" };
const NAVOF = { cont: "map" };
async function go(p, keep, arg) {
  if (!PAGES[p]) p = "today";
  S.page = p; S.arg = arg != null ? String(arg) : null;
  if (!keep) { $("#main").innerHTML = '<div class="empty">Загружаю…</div>'; S.sel.clear(); updSel(); }
  $$(".tab[data-page]").forEach((n) => n.classList.toggle("on", n.dataset.page === (NAVOF[p] || p)));
  $("#pageTitle").textContent = TITLES[p];
  $("#siteSel").hidden = $("#clientSel").hidden = p === "pools" || p === "cont" || p === "calc" || p === "notify" || p === "energy";
  $("#pageAside").innerHTML = "";
  // Каждый переход — шаг в истории браузера: «Назад» (кнопка или браузерная) возвращает на прошлую страницу с её фильтрами.
  const h = "#" + p + (S.arg ? "/" + S.arg : ""), depth = (history.state && history.state.d) || 0;
  if (location.hash !== h) {
    if (keep || !location.hash) history.replaceState({ d: depth }, "", h);
    else { history.replaceState(Object.assign({}, history.state, { d: depth, f: JSON.stringify(S.f), ef: JSON.stringify(S.ef) }), "", location.hash); history.pushState({ d: depth + 1 }, "", h); }
  }
  const bk = $("#backBtn"); if (bk) bk.hidden = !((history.state && history.state.d) > 0);
  try { await PAGES[p](); } catch (e) { if (S.page !== p) return; if (e.message === "auth") return showLogin(e.status === 403 ? "Ферма доступна администраторам. Войдите под нужным Telegram." : ""); $("#main").innerHTML = `<div class="empty">Не удалось загрузить: ${esc(e.message)}</div>`; console.error(e); }
}
function filtConts() { return S.sum.containers.filter((c) => !MS.site.size || MS.site.has(String(c.site_id))); }

// ---------- overview ----------
function aggregate(devs) {
  return devs.reduce((a, d) => { a.total++; a[d.status] = (a[d.status] || 0) + 1; if (d.status === "working") { a.ths += d.ths || 0; a.nom += d.nominal || 0; } a.kw += (d.w || 0) / 1000; if (d.alarms.length) a.al++; return a; }, { total: 0, working: 0, offline: 0, suspended: 0, ths: 0, nom: 0, kw: 0, al: 0 });
}
// Low hashrate считает сервер по среднему за последний час (порог в настройках, по умолчанию 90% заводского).
const isLow = (d) => d.status === "working" && d.alarms.some((a) => a.code === "LOWHASH");
const lowPct = () => (S.cfg && S.cfg.lowhash_pct) || 90;
const isHot = (d) => d.temp != null && d.temp >= 80;
async function pageToday() {
  const seq = S.seq = (S.seq || 0) + 1;
  await loadSummary();
  const [devs] = await Promise.all([loadDevices(), loadLayouts(), loadCfg()]);
  const s = S.sum, agg = aggregate(devs);
  const eff = agg.nom ? agg.ths / agg.nom * 100 : null;
  const pw = (x) => (agg.total ? (x || 0) / agg.total * 100 : 0);
  // Аварии — только по контейнерам и площадкам, где есть асики выбранных клиентов/площадок.
  const filtOn = MS.site.size || MS.client.size;
  const openInc = s.incidents.filter((i) => !i.ended && (!filtOn || devs.some((d) => (i.kind === "site" ? d.site_id : d.container_id) === i.scope_id)));
  // containers from filtered devices
  const cmap = {}; devs.forEach((d) => { const c = cmap[d.container_id || 0] = cmap[d.container_id || 0] || { id: d.container_id, name: d.container || "Без контейнера", site: d.site, total: 0, working: 0, offline: 0, suspended: 0, ths: 0, kw: 0, tmax: null }; c.total++; c[d.status] = (c[d.status] || 0) + 1; if (d.status === "working") c.ths += d.ths || 0; c.kw += (d.w || 0) / 1000; if (d.temp != null) c.tmax = Math.max(c.tmax ?? -99, d.temp); });
  const conts = Object.values(cmap).sort((a, b) => b.offline / b.total - a.offline / a.total || b.offline - a.offline);
  const clm = {}; devs.forEach((d) => { const k = clm[d.client_id || 0] = clm[d.client_id || 0] || { id: d.client_id, name: d.client || "Без клиента", total: 0, working: 0, offline: 0, ths: 0, kw: 0 }; k.total++; if (d.status === "working") { k.working++; k.ths += d.ths || 0; } if (d.status === "offline") k.offline++; k.kw += (d.w || 0) / 1000; });
  const clients = Object.values(clm).sort((a, b) => b.total - a.total);
  // attention list
  const att = [];
  openInc.forEach((i) => att.push({ col: "bad", t: `Авария: ${i.scope}`, s: `${nf(i.down)} из ${nf(i.total)} офлайн с ${tfmt(i.started)}`, n: i.down, go: ["map"] }));
  conts.filter((c) => c.id && c.offline >= 5 && c.offline / c.total >= 0.1).slice(0, 6).forEach((c) => att.push({ col: c.offline / c.total >= 0.8 ? "bad" : "warn", t: c.name, s: `не работают ${nf(c.offline)} из ${nf(c.total)}`, n: c.offline, go: ["cont", c.id] }));
  const low = devs.filter(isLow).length, hot = devs.filter(isHot).length;
  if (low) att.push({ col: "warn", t: "Low hashrate", s: `ниже ${lowPct()}% заводского в среднем за час`, n: low, go: ["asics", "low"] });
  if (hot) att.push({ col: "avg", t: "Горячие, 80° и выше", s: "проверьте охлаждение", n: hot, go: ["asics", "hot"] });
  s.classes.filter((c) => c.count && c.key !== "offline").forEach((c) => att.push({ col: "mut", t: `Ошибки: ${c.label.toLowerCase()}`, s: "открыть список", n: c.count, go: ["errors", c.key] }));
  const noPlace = devs.filter((d) => d.container_id && !placeOf(d)).length;
  if (noPlace) att.push({ col: "mut", t: "Без места в раскладке", s: "IP не совпал с местом — поправьте раскладку", n: noPlace, go: ["asics", "noplace"] });
  if (S.page !== "today" || seq !== S.seq) return;
  $("#main").innerHTML = `
  <div class="kpis">
    <button class="k click" data-q="working" title="Сколько асиков сейчас отдают хешрейт"><div class="l">Работают</div><div class="v ok">${nf(agg.working)}<small>из ${nf(agg.total)}</small></div>
      <div class="bar"><span style="width:${pw(agg.working)}%;background:var(--ok)"></span><span style="width:${pw(agg.suspended)}%;background:var(--susp)"></span><span style="width:${pw(agg.offline)}%;background:var(--bad)"></span></div></button>
    <button class="k click" data-q="offline" title="Нет связи с асиком"><div class="l">Не работают</div><div class="v bad">${nf(agg.offline)}</div><div class="s">${nf(pw(agg.offline), 1)}% парка${agg.suspended ? ` · сон ${nf(agg.suspended)}` : ""}</div></button>
    <div class="k" title="Сумма текущего хешрейта работающих асиков"><div class="l">Хешрейт</div><div class="v">${nf(agg.ths / 1000, 1)}<small>PH/s</small></div><div class="s">${eff ? nf(eff, 0) + "% от заводского" : "—"}</div></div>
    <div class="k" title="Сумма потребления по данным асиков"><div class="l">Потребление</div><div class="v">${nf(agg.kw / 1000, 2)}<small>МВт</small></div><div class="s">≈ ${nf(agg.kw * 24 / 1000, 0)} МВт·ч в сутки</div></div>
    <button class="k click" data-go="errors" title="Асики, у которых сейчас есть хотя бы одна ошибка"><div class="l">С ошибками</div><div class="v warn">${nf(agg.al)}</div><div class="s">открыть проблемы →</div></button>
    <div class="k"><div class="l">Аварии</div><div class="v ${openInc.length ? "bad" : "ok"}">${openInc.length || "нет"}</div><div class="s">${openInc.length ? esc(openInc[0].scope) : "контейнеры не падали целиком"}</div></div>
  </div>
  <div class="grid">
    <div class="w c8 ovr"><div class="wh"><span class="seg" id="chSer"><button data-s="ths" class="on">Хешрейт</button><button data-s="online">Асики в работе</button><button data-s="kw">Потребление</button></span><button class="btn sm per" id="chPer" title="Сутки, неделя, месяц или свои даты">Период ▾</button><span class="sp"></span><span class="seg" id="chRange"><button data-h="6">6 ч</button><button data-h="24" class="on">24 ч</button><button data-h="168">7 дней</button><button data-h="720">30 дней</button></span></div><div class="cstats" id="hrStats"></div><div class="wb chartbox" id="hrChart"></div></div>
    <div class="w c4 ovr"><div class="wh">Требует внимания<span class="sp"></span><span class="mini">${att.length ? nf(att.length) + " пунктов" : ""}</span></div><div class="wb att">
      ${att.map((a, i) => `<button class="arow" data-att="${i}"><i class="dot" style="background:var(--${a.col})"></i><span class="at"><b>${esc(a.t)}</b><span>${esc(a.s)}</span></span><span class="num an">${nf(a.n)}</span><span class="chev">›</span></button>`).join("") || '<div class="okbox">Всё спокойно: аварий, перегрева и просадок нет.</div>'}
    </div></div>
    <div class="w c7"><div class="wh">${nf(conts.length)} ${plural(conts.length, ["контейнер", "контейнера", "контейнеров"])}<span class="sp"></span><span class="mini">клик по заголовку — сортировка · строка — открыть контейнер</span></div><div id="dtC"></div></div>
    <div class="w c5"><div class="wh">${nf(clients.length)} ${plural(clients.length, ["клиент", "клиента", "клиентов"])} хостинга<span class="sp"></span><span class="mini">«В работе» — доля асиков, что майнят сейчас</span></div><div id="dtK"></div></div>
  </div>`;
  $$(".k[data-q]").forEach((x) => x.onclick = () => { resetF(); S.f.status = x.dataset.q; go("asics"); });
  $$(".k[data-go]").forEach((x) => x.onclick = () => go(x.dataset.go));
  $$(".arow").forEach((x) => x.onclick = () => { const a = att[+x.dataset.att]; if (a.go[0] === "asics") { resetF(); S.f.quick = a.go[1]; return go("asics"); } if (a.go[0] === "errors") { S.ef.cls = a.go[1]; return go("errors"); } go(a.go[0], false, a.go[1]); });
  const bar = (c) => `<div class="mbar"><span style="width:${c.working / c.total * 100}%;background:var(--ok)"></span><span style="width:${(c.suspended || 0) / c.total * 100}%;background:var(--susp)"></span><span style="width:${c.offline / c.total * 100}%;background:var(--bad)"></span></div><span class="num small">${nf(c.working)} / ${nf(c.total)}</span>`;
  const kindOf = (c) => { const L = c.id && S.lay[c.id]; return L ? L.layout.kind : ""; };
  DT($("#dtC"), { id: "tC", title: "Контейнеры", rows: conts, sort: "-offp", maxH: "440px", rowAttr: (c) => `data-cont="${c.id || ""}"`,
    cols: [{ k: "name", t: "Контейнер", v: (c) => c.name, h: (c) => `<span class="lnk" title="${esc(c.name)}">${esc(c.name)}</span>`, f: "text" },
      { k: "kind", t: "Тип", v: (c) => KIND[kindOf(c)] || "", h: (c) => kindOf(c) ? `<span class="kind k-${kindOf(c)}">${KIND[kindOf(c)]}</span>` : "", f: "sel" },
      { k: "workp", t: "Работают", w: "150px", v: (c) => c.working / c.total, h: bar, p: (c) => `${c.working} / ${c.total}`, f: "num", fv: (c) => c.working },
      { k: "offp", t: "Не работают", r: 1, v: (c) => c.offline / c.total * 1e6 + c.offline, h: (c) => `<span class="${c.offline ? "bad" : "faint"}">${nf(c.offline)}</span>`, p: (c) => c.offline, f: "num", fv: (c) => c.offline },
      { k: "ths", t: "PH/s", r: 1, v: (c) => c.ths / 1000, h: (c) => nf(c.ths / 1000, 2), f: "num" },
      { k: "kw", t: "кВт", r: 1, v: (c) => c.kw, h: (c) => nf(c.kw, 0), f: "num" },
      { k: "tmax", t: "t° макс", r: 1, v: (c) => c.tmax, h: (c) => `<span class="${c.tmax >= 80 ? "warn" : ""}">${c.tmax == null ? "—" : nf(c.tmax, 0)}</span>`, f: "num" }] });
  DT($("#dtK"), { id: "tK", title: "Клиенты хостинга", rows: clients, sort: "-total", maxH: "440px", rowAttr: (c) => `data-client="${c.id || ""}"`,
    cols: [{ k: "name", t: "Клиент", v: (c) => (c.name || "").trim(), h: (c) => `<span class="lnk">${esc((c.name || "").trim())}</span>`, f: "text" },
      { k: "total", t: "Асиков", r: 1, v: (c) => c.total, h: (c) => nf(c.total), f: "num" },
      { k: "wp", t: "В работе", r: 1, v: (c) => c.total ? c.working / c.total * 100 : 0, h: (c) => { const p = c.total ? c.working / c.total * 100 : 0; return `<span class="pct ${p >= 95 ? "p-ok" : p >= 80 ? "p-warn" : "p-bad"}">${nf(p, 0)}%</span>`; }, f: "num" },
      { k: "offline", t: "Не работают", r: 1, v: (c) => c.offline, h: (c) => `<span class="${c.offline ? "bad" : "faint"}">${nf(c.offline)}</span>`, p: (c) => c.offline, f: "num" },
      { k: "ths", t: "PH/s", r: 1, v: (c) => c.ths / 1000, h: (c) => nf(c.ths / 1000, 2), f: "num" }] });
  $("#dtC").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-cont]"); if (tr && tr.dataset.cont) go("cont", false, tr.dataset.cont); });
  $("#dtK").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-client]"); if (tr && tr.dataset.client) { setClient(tr.dataset.client); S.devs = null; go("today"); } });
  const ch = S.ch = S.ch || { s: "ths", h: 24 };
  $$("#chSer button").forEach((x) => x.classList.toggle("on", x.dataset.s === ch.s));
  $$("#chRange button").forEach((x) => x.classList.toggle("on", !ch.start && +x.dataset.h === ch.h));
  if (ch.start) { $("#chPer").textContent = ch.label + " ▾"; $("#chPer").classList.add("on"); }
  periodBtn($("#chPer"), ch, (p) => { Object.assign(ch, p); $("#chPer").textContent = p.label + " ▾"; $("#chPer").classList.add("on"); $$("#chRange button").forEach((x) => x.classList.remove("on")); drawFarm(ch); });
  $$("#chSer button").forEach((b) => b.onclick = () => { $$("#chSer button").forEach((x) => x.classList.toggle("on", x === b)); ch.s = b.dataset.s; drawFarm(ch); });
  $$("#chRange button").forEach((b) => b.onclick = () => { $$("#chRange button").forEach((x) => x.classList.toggle("on", x === b)); ch.h = +b.dataset.h; delete ch.start; delete ch.end; delete ch.label; $("#chPer").textContent = "Период ▾"; $("#chPer").classList.remove("on"); drawFarm(ch); });
  drawFarm(ch);
  // при смене ширины окна (поворот телефона, боковая панель) перерисовываем, а не растягиваем картинку
  let rw = $("#hrChart").clientWidth; const ro = new ResizeObserver(() => { const el = $("#hrChart"); if (!el) return ro.disconnect(); if (Math.abs(el.clientWidth - rw) > 8) { rw = el.clientWidth; clearTimeout(ro.t); ro.t = setTimeout(() => drawFarm(ch), 150); } });
  ro.observe($("#hrChart"));
}
const SER = { ths: { k: "ths", unit: "PH/s", f: (v) => v / 1000, d: 1, name: "Хешрейт" }, online: { k: "online", unit: "асиков", f: (v) => v, d: 0, name: "В работе" }, kw: { k: "kw", unit: "МВт", f: (v) => v / 1000, d: 2, name: "Потребление" } };
async function drawFarm(ch) {
  const el = $("#hrChart"); if (!el) return;
  const H = periodHours(ch);
  const pts = await api("/farm/history?" + qs(ch.start ? { start: ch.start, end: ch.end, site: site() } : { hours: ch.h, site: site() }));
  if (!$("#hrChart")) return;
  if (pts.length < 2) { el.innerHTML = `<div class="hint">${ch.start ? "За этот период истории нет." : "Копим историю: точка добавляется каждую минуту."}</div>`; $("#hrStats").innerHTML = ""; return; }
  const sr = SER[ch.s];
  const vals = pts.map((p) => sr.f(p[sr.k])); const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  let mi = 0; vals.forEach((v, i) => { if (v < vals[mi]) mi = i; });
  const span = (new Date(pts[pts.length - 1].at) - new Date(pts[0].at)) / 3.6e6;
  const short = span < H * 0.9 && !(ch.start && ch.start === mskDay(0));
  const per = ch.start ? ch.label.toLowerCase() : short ? "всё время (" + (span < 48 ? Math.max(1, Math.round(span)) + " ч" : Math.round(span / 24) + " дн") + ")" : { 6: "6 ч", 24: "24 ч", 168: "7 дней", 720: "30 дней" }[ch.h] || ch.h + " ч";
  const tm0 = (t) => new Date(t).toLocaleString("ru-RU", Object.assign({ day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }, MSK));
  const tm = (t) => new Date(t).toLocaleString("ru-RU", Object.assign(H > 36 ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" }, MSK));
  $("#hrStats").innerHTML = `<span>Сейчас <b class="num">${nf(vals[vals.length - 1], sr.d)}</b> ${sr.unit}</span><span>Среднее за ${per} <b class="num">${nf(avg, sr.d)}</b></span><span>Минимум <b class="num">${nf(vals[mi], sr.d)}</b> <span class="faint">в ${tm(pts[mi].at)}</span></span>${short ? `<span class="faint">история копится с ${tm0(pts[0].at)}</span>` : ""}${client() ? '<span class="faint">график — по площадке целиком, без фильтра клиента</span>' : ""}`;
  mountChart(el, pts.map((p) => [new Date(p.at).getTime(), sr.f(p[sr.k])]), { unit: sr.unit, d: sr.d, name: sr.name, h: H, extra: ch.s === "online" ? pts.map((p) => p.total) : null });
}
function mountChart(el, pts, o) {
  const W = Math.max(320, el.clientWidth - 4), H = Math.max(200, el.clientHeight - 4), P = { l: 54, r: 12, t: 14, b: 24 };
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  // Ось всегда от нуля и с ровными делениями — как у пулов: колебание в 1% не выглядит обрывом.
  const nice = (v) => { if (!(v > 0)) return 1; const e = 10 ** Math.floor(Math.log10(v)), m = v / e; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 4 ? 4 : m <= 5 ? 5 : m <= 8 ? 8 : 10) * e; };
  let y0 = 0, y1 = nice(Math.max(...ys) * 1.1);
  const X = (x) => P.l + (x - x0) / (x1 - x0 || 1) * (W - P.l - P.r), Y = (y) => H - P.b - (y - y0) / (y1 - y0 || 1) * (H - P.t - P.b);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join("");
  const dd = y1 / 4 < 1 ? 2 : y1 / 4 < 10 && y1 / 4 % 1 ? 1 : 0;
  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => { const v = y0 + (y1 - y0) * f; return `<line x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l - 6}" y="${Y(v) + 3}" text-anchor="end">${nf(v, dd)}</text>`; }).join("");
  const long = (x1 - x0) > 36 * 3600e3;
  const tf = (t) => new Date(t).toLocaleString("ru-RU", Object.assign(long ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" }, MSK));
  const nt = Math.max(2, Math.min(7, Math.floor(W / 130)));
  const tx = Array.from({ length: nt }, (_, i) => x0 + (x1 - x0) * i / (nt - 1)).map((t, i) => `<text x="${X(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === nt - 1 ? "end" : "middle"}">${tf(t)}</text>`).join("");
  el.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".22"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>${grid}${tx}<path class="a" fill="url(#cg)" d="${d}L${X(x1)} ${H - P.b}L${X(x0)} ${H - P.b}Z"/><path class="l" d="${d}"/><text x="${W - P.r}" y="10" text-anchor="end">${o.unit}</text><line class="cx" x1="0" x2="0" y1="${P.t}" y2="${H - P.b}" style="display:none"/><circle class="cd" r="3.5" style="display:none"/></svg><div class="ctip" hidden></div>`;
  const svg = $("svg", el), tip = $(".ctip", el), cx = $(".cx", el), cd = $(".cd", el);
  svg.onmousemove = (e) => {
    const r = svg.getBoundingClientRect(); const mx = e.clientX - r.left; let bi = 0, bd = 1e18;
    pts.forEach((p, i) => { const dx = Math.abs(X(p[0]) - mx); if (dx < bd) { bd = dx; bi = i; } });
    const p = pts[bi]; const px = X(p[0]), py = Y(p[1]);
    cx.setAttribute("x1", px); cx.setAttribute("x2", px); cx.style.display = cd.style.display = ""; cd.setAttribute("cx", px); cd.setAttribute("cy", py);
    tip.hidden = false; tip.innerHTML = `<b class="num">${nf(p[1], o.d)} ${o.unit}</b>${o.extra ? ` <span class="faint">из ${nf(o.extra[bi])}</span>` : ""}<br><span class="faint">${new Date(p[0]).toLocaleString("ru-RU", Object.assign({ day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }, MSK))}</span>`;
    tip.style.left = Math.min(px + 12, W - 150) + "px"; tip.style.top = Math.max(0, py - 44) + "px";
  };
  svg.onmouseleave = () => { tip.hidden = true; cx.style.display = cd.style.display = "none"; };
}
function lineChart(pts, unit) { const box = document.createElement("div"); box.className = "chartbox sm"; box.style.width = "560px"; document.body.appendChild(box); mountChart(box, pts, { unit, d: 1 }); const h = box.innerHTML; box.remove(); return `<div class="chartbox sm">${h}</div>`; }

const MODES = { normal: "обычный", low: "тихий", high: "разгон", sleep: "сон", eco: "эко", unknown: "—" };
function modeRu(m) { return m ? (MODES[String(m).toLowerCase()] || m) : "—"; }

// ---------- colouring ----------
const PAL = ["#4F98A3", "#E0A33A", "#8D78C4", "#5FB878", "#E07B3A", "#6C8EEF", "#C46FA0", "#9FB34A", "#D9625A", "#47B3C9", "#B38A5A", "#7F8C99"];
function hashCol(s) { let h = 0; for (const c of String(s || "")) h = (h * 31 + c.charCodeAt(0)) >>> 0; return PAL[h % PAL.length]; }
function cellColor(d, layer) {
  if (!d) return "";
  if (layer === "temp") { if (d.temp == null) return "var(--off)"; const t = d.temp; return t >= 85 ? "var(--bad)" : t >= 75 ? "var(--avg)" : t >= 65 ? "var(--warn)" : t >= 45 ? "var(--ok)" : "var(--info)"; }
  if (layer === "eff") { if (d.status !== "working" || !d.nominal) return "var(--off)"; const p = d.ths / d.nominal; return p >= 0.95 ? "var(--ok)" : p >= 0.85 ? "var(--warn)" : p >= 0.6 ? "var(--avg)" : "var(--bad)"; }
  if (layer === "model") return hashCol(d.model);
  if (layer === "client") return hashCol(d.client);
  return d.status === "working" ? (d.alarms.length ? "var(--okw)" : "var(--ok)") : d.status === "suspended" ? "var(--susp)" : d.status === "offline" ? "var(--bad)" : "var(--off)";
}
const LAYERS = [["status", "Статус"], ["temp", "Температура"], ["eff", "% от заводского"], ["model", "Модель"], ["client", "Клиент"]];
function legendFor(layer, devs, cnt, lf) {
  const it = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
  if (layer === "temp") return it("var(--info)", "&lt;45°") + it("var(--ok)", "45–65°") + it("var(--warn)", "65–75°") + it("var(--avg)", "75–85°") + it("var(--bad)", "85°+") + it("var(--off)", "нет данных");
  if (layer === "eff") return it("var(--ok)", "95%+") + it("var(--warn)", "85–95%") + it("var(--avg)", "60–85%") + it("var(--bad)", "&lt;60%") + it("var(--off)", "не работает");
  if (layer === "model" || layer === "client") { const k = layer; const cnt = {}; (devs || []).forEach((d) => { cnt[d[k] || "—"] = (cnt[d[k] || "—"] || 0) + 1; }); return Object.entries(cnt).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([n, c]) => it(hashCol(n === "—" ? null : n), `${esc(n)} <span class="faint">${c}</span>`)).join(""); }
  if (cnt) { const b = (k, c, t) => `<button class="lgb ${lf && lf.has(k) ? "on" : ""} ${lf && lf.size && !lf.has(k) ? "off" : ""}" data-lf="${k}" ${cnt[k] ? "" : "disabled"}><i ${c ? `style="background:${c}"` : 'class="empty-i"'}></i>${t} <b class="num">${nf(cnt[k] || 0)}</b></button>`;
    return b("ok", "var(--ok)", "работает") + b("okw", "var(--okw)", "работает с ошибкой") + b("bad", "var(--bad)", "не работает") + b("susp", "var(--susp)", "сон") + b("empty", "", "пустое место"); }
  return it("var(--ok)", "работает") + it("var(--okw)", "работает с ошибкой") + it("var(--bad)", "не работает") + it("var(--susp)", "сон") + `<span><i class="empty-i"></i>пустое место</span>`;
}
// ключ статуса для легенды-фильтра
const lfKey = (d) => d.status === "working" ? (d.alarms.length ? "okw" : "ok") : d.status === "offline" ? "bad" : d.status === "suspended" ? "susp" : "off";
function tipHtml(d) {
  return `<b>${esc(d.model || "")}</b> ${stPill(d.status)}<br><span class="faint">${esc(placeTxt(d))}</span><br><span class="num">${esc(d.ip || "без IP")}</span> · ${d.status === "working" ? `<span class="num">${nf(d.ths, 1)} TH/s</span>` : ""}${d.temp != null ? ` · <span class="num">${nf(d.temp, 0)}°</span>` : ""}<br><span class="faint num">${esc(d.worker || "")}</span>${d.client ? `<br>${esc(d.client)}` : ""}${d.alarms.length ? `<br><span class="warn">⚠ ${esc(d.alarms.map((a) => a.msg).join("; "))}</span>` : ""}`;
}
const tipEl = () => $("#tip");
document.addEventListener("mouseover", (e) => { const c = e.target.closest && e.target.closest("[data-tip]"); const t = tipEl(); if (!c) { t.hidden = true; return; } const d = S.devMap && S.devMap.get(+c.dataset.tip); if (!d) { t.hidden = true; return; } t.innerHTML = tipHtml(d); t.hidden = false; });
document.addEventListener("mousemove", (e) => { const t = tipEl(); if (t.hidden) return; const x = Math.min(e.clientX + 14, innerWidth - t.offsetWidth - 8), y = Math.min(e.clientY + 14, innerHeight - t.offsetHeight - 8); t.style.left = x + "px"; t.style.top = y + "px"; });

// ---------- containers overview ----------
function miniLayout(L, devsById, layer) {
  if (!L) return "";
  const secs = L.layout.sections; const grid = secs.map(() => ({}));
  Object.entries(L.placed).forEach(([id, p]) => { grid[p[0]][p[1] + ":" + p[2] + ":" + p[3]] = +id; });
  return `<div class="mini-l ml-${L.layout.kind}">${secs.map((s, si) => `<div class="ml-sec">${Array.from({ length: s.racks }, (_, r) => `<div class="ml-rack" style="grid-template-columns:repeat(${s.places},1fr)">${Array.from({ length: s.shelves * s.places }, (_, j) => { const sh = Math.floor(j / s.places), sl = j % s.places; const id = grid[si][r + ":" + sh + ":" + sl]; const d = id && devsById.get(id); const num = (s.first || 1) + r * s.shelves * s.places + j; if (s.last && num > s.last) return '<i class="x"></i>'; return d ? `<i style="background:${cellColor(d, layer)}"></i>` : '<i class="e"></i>'; }).join("")}</div>`).join("")}</div>`).join("")}</div>`;
}
async function pageMap() {
  const seq = S.seq = (S.seq || 0) + 1;
  await loadSummary();
  const [devs] = await Promise.all([loadDevices(), loadLayouts(true)]);
  const mf = S.mf; const by = {};
  devs.forEach((d) => { (by[d.container_id || 0] = by[d.container_id || 0] || []).push(d); });
  let conts = filtConts().filter((c) => by[c.id || 0] && c.id);
  const kinds = {}; conts.forEach((c) => { const L = S.lay[c.id]; if (L) kinds[L.layout.kind] = (kinds[L.layout.kind] || 0) + 1; });
  if (mf.kind && !kinds[mf.kind]) mf.kind = "";
  if (mf.kind) conts = conts.filter((c) => S.lay[c.id] && S.lay[c.id].layout.kind === mf.kind);
  const st = (c) => { const l = by[c.id]; const w = l.filter((d) => d.status === "working").length, off = l.filter((d) => d.status === "offline").length; return { w, off, n: l.length, ths: l.reduce((a, d) => a + (d.status === "working" ? d.ths || 0 : 0), 0), kw: l.reduce((a, d) => a + (d.w || 0), 0) / 1000, tmax: Math.max(-99, ...l.map((d) => d.temp ?? -99)), al: l.filter((d) => d.alarms.length && d.status === "working").length }; };
  const stats = new Map(conts.map((c) => [c.id, st(c)]));
  const natural = (a, b) => a.name.localeCompare(b.name, "ru", { numeric: true });
  conts.sort(mf.sort === "problems" ? (a, b) => stats.get(b.id).off / stats.get(b.id).n - stats.get(a.id).off / stats.get(a.id).n || natural(a, b) : natural);
  const html = conts.map((c) => { const L = S.lay[c.id]; const x = stats.get(c.id);
    return `<button class="cbox" data-c="${c.id}"><div class="cb-h"><span class="kind k-${L ? L.layout.kind : "air"}">${L ? KIND[L.layout.kind] : ""}</span><span class="nm" title="${esc(c.name)}">${esc(c.name)}</span></div>
      <div class="cb-m"><span><b class="num ok">${nf(x.w)}</b><span class="faint"> / ${nf(x.n)}</span></span>${x.off ? `<span class="bad num">✕ ${nf(x.off)}</span>` : '<span class="faint">все в работе</span>'}${x.al ? `<span class="warn num">⚠ ${nf(x.al)}</span>` : ""}<span class="sp"></span><span class="num">${nf(x.ths / 1000, 2)} PH</span><span class="num faint">${nf(x.kw, 0)} кВт</span>${x.tmax > -99 ? `<span class="num ${x.tmax >= 80 ? "warn" : "faint"}">${nf(x.tmax, 0)}°</span>` : ""}</div>
      ${miniLayout(L, S.devMap, mf.layer)}
      <div class="cb-f faint">${L ? `${L.layout.kind === "hydro" ? "секций" : L.layout.kind === "bath" ? "ванн" : "подсетей"}: ${L.layout.sections.length} · мест ${nf(L.capacity)} · свободно ${nf(Math.max(0, L.capacity - Object.keys(L.placed).length))}${L.unplaced.length ? ` · без места ${L.unplaced.length}` : ""}` : ""}<span class="sp"></span><span class="go">открыть →</span></div></button>`; }).join("");
  if (S.page !== "map" || seq !== S.seq) return;
  $("#pageAside").innerHTML = `<span class="seg" id="mKind"><button data-k="" class="${!mf.kind ? "on" : ""}">Все ${Object.values(kinds).reduce((x, y) => x + y, 0)}</button>${Object.entries(kinds).map(([k, n]) => `<button data-k="${k}" class="${mf.kind === k ? "on" : ""}">${KIND[k]} ${n}</button>`).join("")}</span>
    <span class="seg" id="mSort"><button data-s="problems" class="${mf.sort === "problems" ? "on" : ""}">Сначала проблемные</button><button data-s="name" class="${mf.sort === "name" ? "on" : ""}">По номеру</button></span>`;
  $("#main").innerHTML = `<div class="fbar"><span class="lbl">Чем красить</span><span class="seg" id="mLayer">${LAYERS.map(([k, t]) => `<button data-l="${k}" class="${mf.layer === k ? "on" : ""}">${t}</button>`).join("")}</span><div class="legend">${legendFor(mf.layer, devs)}</div></div>
    <div class="cmap">${html || '<div class="empty">Нет контейнеров под фильтр</div>'}</div>`;
  $$("#mKind button").forEach((b) => b.onclick = () => { mf.kind = b.dataset.k; pageMap(); });
  $$("#mSort button").forEach((b) => b.onclick = () => { mf.sort = b.dataset.s; pageMap(); });
  $$("#mLayer button").forEach((b) => b.onclick = () => { mf.layer = b.dataset.l; pageMap(); });
  $$(".cbox[data-c]").forEach((b) => b.onclick = () => go("cont", false, b.dataset.c));
}

// ---------- one container ----------
async function pageCont(fast) {
  const seq = S.seq = (S.seq || 0) + 1;
  const cid = +S.arg;
  await loadSummary();
  const lc = S.lc && S.lc.cid === cid && Date.now() - S.lc.at < 55000 && fast ? S.lc.L : null;
  const [devs, L] = await Promise.all([loadDevices(), lc || api(`/farm/containers/${cid}/layout`)]);
  if (!lc) S.lc = { cid, L, at: Date.now() };
  S.lay = S.lay || {}; S.lay[cid] = L;
  // Контейнер — физический вид: показываем все его места, без фильтра клиента.
  void devs; await loadCfg(); const list = S.all.filter((d) => d.container_id === cid);
  const cl = S.cl; const layer = cl.layer;
  if (cl.cid !== cid) { cl.cid = cid; cl.lf = new Set(); cl.err = ""; }
  // Фильтр по легенде и ошибкам: на схеме гасит лишнее, ниже — тот же список асиков.
  const ERR = { low: ["Low hashrate", isLow], hot: ["Горячие 80°+", isHot], noplace: ["Без места", (d) => !placeOf(d)] };
  const clsL = Object.fromEntries(S.sum.classes.map((c) => [c.key, c.label]));
  list.forEach((d) => d.alarms.forEach((a) => { const k = "c:" + (a.cls || "other"); if (!ERR[k]) ERR[k] = [clsL[a.cls || "other"] || "Прочее", (x) => x.alarms.some((b) => (b.cls || "other") === (a.cls || "other"))]; }));
  if (cl.err && !ERR[cl.err]) cl.err = "";
  const okErr = (d) => !cl.err || ERR[cl.err][1](d);
  const okLf = (d) => !cl.lf.size || cl.lf.has(lfKey(d));
  const match = (d) => okErr(d) && okLf(d);
  const lcnt = {}; list.filter(okErr).forEach((d) => { const k = lfKey(d); lcnt[k] = (lcnt[k] || 0) + 1; });
  lcnt.empty = Math.max(0, L.capacity - Object.keys(L.placed).length);
  const agg = aggregate(list); const tl = list.map((d) => d.temp).filter((t) => t != null);
  const secs = L.layout.sections;
  const grid = secs.map(() => ({})); Object.entries(L.placed).forEach(([id, p]) => { grid[p[0]][p[1] + ":" + p[2] + ":" + p[3]] = +id; });
  S.order = [];
  const secHtml = secs.map((s, si) => {
    const racks = Array.from({ length: s.racks }, (_, r) => {
      const rows = Array.from({ length: s.shelves }, (_, sh) => {
        const cells = Array.from({ length: s.places }, (_, sl) => {
          const num = (s.first || 1) + r * s.shelves * s.places + sh * s.places + sl;
          if (s.last && num > s.last) return '<i class="pc x"></i>';
          const id = grid[si][r + ":" + sh + ":" + sl]; const d = id && S.devMap.get(id);
          if (!d) return `<i class="pc e ${cl.lf.size && !cl.lf.has("empty") ? "dim" : ""}" title="Место ${num} свободно"><span>${num}</span></i>`;
          S.order.push(d.id);
          return `<i class="pc ${match(d) ? "" : "dim"}" data-id="${d.id}" data-tip="${d.id}" style="background:${cellColor(d, layer)}"><span>${num}</span></i>`;
        }).join("");
        const shelfNo = L.layout.start === "bottom-left" ? s.shelves - sh : sh + 1;
        return `<div class="shelf"><b class="sh" data-shelf="${si}:${r}:${sh}" title="Выделить полку">${shelfNo}</b><div class="pcs" style="grid-template-columns:repeat(${s.places},var(--pc))">${cells}</div></div>`;
      }).join("");
      return `<div class="rack"><div class="rk" data-rack="${si}:${r}" title="Выделить весь стеллаж">${L.layout.kind === "air" ? "Стеллаж " + ((s.rack_from || 1) + r) : "Полки"}</div>${rows}</div>`;
    }).join("");
    return `<div class="sec2"><div class="sec2-h"><b>${esc(s.title)}</b>${s.subnet != null ? `<span class="faint num">IP 10.x.${s.subnet}.${s.first || 1}–${s.last || (s.first || 1) + s.racks * s.shelves * s.places - 1}</span>` : ""}</div><div class="racks">${racks}</div></div>`;
  }).join("");
  const shape = secs.map((s) => L.layout.kind === "air" ? `${s.racks} стеллажей × ${s.shelves} полок × ${s.places} мест` : `${s.shelves} полок × ${s.places} мест`);
  if (S.page !== "cont" || seq !== S.seq) return;
  $("#pageTitle").innerHTML = `<a href="#map" class="crumb-a">Контейнеры</a> <span class="faint">›</span> ${esc(L.name)}`;
  $("#pageAside").innerHTML = `<span class="kind k-${L.layout.kind}">${KIND[L.layout.kind]}</span><span class="hint">${esc([...new Set(shape)].join(" · "))}${L.layout.auto ? " · определено по IP" : " · ваша раскладка"}</span><button class="btn" id="lEdit">Поправить раскладку</button>`;
  $("#main").innerHTML = `
  <div class="kpis">
    <div class="k"><div class="l">Работают</div><div class="v ok">${nf(agg.working)}<small>из ${nf(agg.total)}</small></div></div>
    <div class="k"><div class="l">Не работают</div><div class="v ${agg.offline ? "bad" : ""}">${nf(agg.offline)}</div><div class="s">${agg.suspended ? "сон " + nf(agg.suspended) : "&nbsp;"}</div></div>
    <div class="k"><div class="l">Хешрейт</div><div class="v">${nf(agg.ths / 1000, 2)}<small>PH/s</small></div><div class="s">${agg.nom ? nf(agg.ths / agg.nom * 100, 0) + "% от заводского" : ""}</div></div>
    <div class="k"><div class="l">Потребление</div><div class="v">${nf(agg.kw, 0)}<small>кВт</small></div><div class="s">≈ ${nf(agg.kw * 24, 0)} кВт·ч в сутки</div></div>
    <div class="k"><div class="l">Температура</div><div class="v">${tl.length ? nf(Math.max(...tl), 0) + "°" : "—"}<small>макс</small></div><div class="s">${tl.length ? "средняя " + nf(tl.reduce((a, b) => a + b, 0) / tl.length, 0) + "°" : ""}</div></div>
    <div class="k"><div class="l">Места</div><div class="v">${nf(Object.keys(L.placed).length)}<small>из ${nf(L.capacity)}</small></div><div class="s">свободно ${nf(Math.max(0, L.capacity - Object.keys(L.placed).length))}</div></div>
  </div>
  <div class="fbar"><span class="lbl">Чем красить</span><span class="seg" id="cLayer">${LAYERS.map(([k, t]) => `<button data-l="${k}" class="${layer === k ? "on" : ""}">${t}</button>`).join("")}</span><div class="legend">${legendFor(layer, list, layer === "status" ? lcnt : null, cl.lf)}</div>
    <span class="sp"></span><label class="tgl"><input type="checkbox" id="selMode" ${cl.selMode ? "checked" : ""}> Режим выделения</label><span class="hint">${cl.selMode ? "клик — добавить/убрать · клик по стеллажу или полке — выделить целиком" : "клик по асику — карточка"}</span></div>
  <div class="w"><div class="wb layout-wrap"><div class="layout l-${L.layout.kind}">${secHtml}</div></div></div>
  <div class="fbar cerr"><span class="lbl">Ошибки</span><div class="chips"><button class="chip ${!cl.err ? "on" : ""}" data-err="">Все <b>${nf(list.length)}</b></button>${Object.entries(ERR).map(([k, [t, fn]]) => { const n = list.filter(fn).length; return n || cl.err === k ? `<button class="chip ${cl.err === k ? "on" : ""}" data-err="${k}">${esc(t)} <b>${nf(n)}</b></button>` : ""; }).join("")}</div>${cl.lf.size || cl.err ? '<button class="btn ghost sm" id="cfX">Сбросить фильтр</button>' : ""}<span class="sp"></span><span class="hint">Low hashrate — ниже ${lowPct()}% заводского в среднем за час</span></div>
  <div class="w" style="margin-top:10px"><div class="wh">Асики контейнера<span class="sp"></span><span class="mini">${cl.lf.size || cl.err ? "по фильтру выше · " : ""}клик — карточка · Ctrl/Shift — выделить</span></div><div id="dtL"></div></div>
  <div class="w" style="margin-top:10px"><div class="wh">Контейнер за период<span class="sp"></span><span class="seg" id="ccH"><button data-h="24" class="on">24 ч</button><button data-h="168">7 дней</button><button data-h="720">30 дней</button></span><span class="seg" id="ccS"><button data-s="online" class="on">Асики в работе</button><button data-s="ths">Хешрейт</button><button data-s="kw">Потребление</button></span></div><div class="wb chartbox" id="cChart" style="height:220px"></div></div>`;
  $$("#cLayer button").forEach((b) => b.onclick = () => { cl.layer = b.dataset.l; pageCont(true); });
  $("#selMode").onchange = (e) => { cl.selMode = e.target.checked; pageCont(true); };
  $("#lEdit").onclick = () => editLayout(cid, L);
  $(".layout").addEventListener("click", (e) => {
    const rk = e.target.closest("[data-rack]"), sh = e.target.closest("[data-shelf]");
    if ((rk || sh) && cl.selMode) { const [si, r, s] = (rk ? rk.dataset.rack : sh.dataset.shelf).split(":").map(Number); Object.entries(L.placed).forEach(([id, p]) => { if (p[0] === si && p[1] === r && (s == null || isNaN(s) || p[2] === s)) S.sel.add(+id); }); markSel(); return; }
    const c = e.target.closest(".pc[data-id]"); if (!c) return; const id = +c.dataset.id;
    if (cl.selMode) { if (e.shiftKey && S.lastIdx != null) return pickId(id, e); S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); S.lastIdx = id; markSel(); return; }
    pickId(id, e) || openCard(id);
  });
  $$(".lgb[data-lf]").forEach((b) => b.onclick = () => { const k = b.dataset.lf; cl.lf.has(k) ? cl.lf.delete(k) : cl.lf.add(k); pageCont(true); });
  $$("[data-err]").forEach((b) => b.onclick = () => { cl.err = b.dataset.err; pageCont(true); });
  const cfx = $("#cfX"); if (cfx) cfx.onclick = () => { cl.lf.clear(); cl.err = ""; pageCont(true); };
  const pk = (d) => { const p = placeOf(d); return p ? p.si * 1e6 + p.rack * 1e4 + p.shelf * 100 + p.num / 1000 : 9e9; };
  DT($("#dtL"), { id: "tL", title: L.name + " — асики", rows: list.filter(match), sort: "place", cb: true, selId: (d) => d.id, rowAttr: (d) => `data-id="${d.id}"`, devIds: (r) => r.map((d) => d.id), cols: devCols(pk, { noCont: true }) });
  tableClicks("#tL"); S.order = S.order.concat([]);
  const cc = { s: "online", h: 24 };
  $$("#ccH button").forEach((b) => b.onclick = () => { $$("#ccH button").forEach((x) => x.classList.toggle("on", x === b)); cc.h = +b.dataset.h; drawC(); });
  const drawC = async () => { const pts = await api(`/farm/containers/${cid}/history?hours=${cc.h}`); const el = $("#cChart"); if (!el) return; if (pts.length < 2) { el.innerHTML = '<div class="hint">История копится.</div>'; return; } const k = cc.s; mountChart(el, pts.map((p) => [new Date(p.at).getTime(), k === "ths" ? p.ths / 1000 : k === "kw" ? p.kw : p.online]), { unit: k === "ths" ? "PH/s" : k === "kw" ? "кВт" : "асиков", d: k === "ths" ? 2 : 0, extra: k === "online" ? pts.map((p) => p.total) : null }); };
  $$("#ccS button").forEach((b) => b.onclick = () => { $$("#ccS button").forEach((x) => x.classList.toggle("on", x === b)); cc.s = b.dataset.s; drawC(); });
  drawC();
  markSel();
}
function editLayout(cid, L) {
  const sp = JSON.parse(JSON.stringify(L.layout));
  const row = (s, i) => `<tr data-i="${i}"><td><input class="in" data-k="title" value="${esc(s.title)}"></td><td><input class="in n" data-k="subnet" value="${s.subnet ?? ""}" placeholder="—"></td><td><input class="in n" data-k="first" value="${s.first || 1}"></td><td><input class="in n" data-k="last" value="${s.last || ""}" placeholder="авто"></td><td><input class="in n" data-k="racks" value="${s.racks}"></td><td><input class="in n" data-k="shelves" value="${s.shelves}"></td><td><input class="in n" data-k="places" value="${s.places}"></td><td><button class="btn ghost" data-del="${i}">✕</button></td></tr>`;
  const draw = () => {
    $("#mdIn").innerHTML = `<div class="dr-h"><div><h2>Раскладка контейнера</h2><div class="mut small">${esc(L.name)}</div></div><span class="sp"></span><button class="btn" id="mdX">Закрыть</button></div>
    <p class="hint">Секция — это подсеть или кабинет. Номер места = последняя цифра IP. Каждая секция: стеллажи × полки × места на полке. Место 1 — ${sp.start === "bottom-left" ? "внизу слева" : "вверху слева"}.</p>
    <div class="fbar"><label class="mini">Тип <select class="sel" id="lKind">${Object.entries(KIND).map(([k, t]) => `<option value="${k}" ${sp.kind === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
    <label class="mini">Место 1 <select class="sel" id="lStart"><option value="top-left" ${sp.start !== "bottom-left" ? "selected" : ""}>вверху слева</option><option value="bottom-left" ${sp.start === "bottom-left" ? "selected" : ""}>внизу слева</option></select></label></div>
    <table class="tbl"><thead><tr><th>Секция</th><th>Подсеть (3-я цифра IP)</th><th>Первое место</th><th>Последнее</th><th>Стеллажей</th><th>Полок</th><th>Мест на полке</th><th></th></tr></thead><tbody>${sp.sections.map(row).join("")}</tbody></table>
    <div class="fbar" style="margin-top:10px"><button class="btn" id="lAdd">+ секция</button><span class="sp"></span>${L.layout.auto ? "" : '<button class="btn ghost" id="lReset">Вернуть автоматическую</button>'}<button class="btn pri" id="lSave">Сохранить</button></div>`;
    $("#mdX").onclick = () => ($("#modal").hidden = true);
    $$("#mdIn input.in").forEach((inp) => inp.onchange = () => { const i = +inp.closest("tr").dataset.i, k = inp.dataset.k; sp.sections[i][k] = k === "title" ? inp.value : inp.value === "" ? null : +inp.value; });
    $$("#mdIn [data-del]").forEach((b) => b.onclick = () => { sp.sections.splice(+b.dataset.del, 1); draw(); });
    $("#lAdd").onclick = () => { const l = sp.sections[sp.sections.length - 1] || { racks: 5, shelves: 6, places: 5 }; sp.sections.push({ title: "Новая секция", subnet: l.subnet != null ? l.subnet + 1 : null, first: 1, racks: l.racks, shelves: l.shelves, places: l.places, rack_from: (l.rack_from || 1) + l.racks }); draw(); };
    $("#lKind").onchange = (e) => (sp.kind = e.target.value);
    $("#lStart").onchange = (e) => { sp.start = e.target.value; };
    $("#lSave").onclick = async () => { await api(`/farm/containers/${cid}/layout`, { method: "PUT", body: JSON.stringify(sp) }); $("#modal").hidden = true; toast("Раскладка сохранена"); S.lay = null; go("cont", false, cid); };
    const rs = $("#lReset"); if (rs) rs.onclick = async () => { await api(`/farm/containers/${cid}/layout`, { method: "PUT", body: JSON.stringify({ reset: true }) }); $("#modal").hidden = true; S.lay = null; go("cont", false, cid); };
  };
  draw(); $("#modal").hidden = false;
}

// ---------- selection ----------
function pickId(id, e) {
  if (e.shiftKey && S.lastIdx != null) {
    const a = S.order.indexOf(S.lastIdx), b = S.order.indexOf(id);
    if (a >= 0 && b >= 0) { const [i, j] = a < b ? [a, b] : [b, a]; S.order.slice(i, j + 1).forEach((x) => S.sel.add(x)); markSel(); return true; }
  }
  if (e.ctrlKey || e.metaKey) { S.sel.has(id) ? S.sel.delete(id) : S.sel.add(id); S.lastIdx = id; markSel(); return true; }
  S.lastIdx = id; return false;
}
function markSel() {
  $$(".pc[data-id]").forEach((c) => c.classList.toggle("on", S.sel.has(+c.dataset.id)));
  $$("tr[data-id]").forEach((r) => { const on = S.sel.has(+r.dataset.id); r.classList.toggle("on", on); const cb = $("input", r); if (cb) cb.checked = on; });
  updSel();
}
function updSel() { $("#selbar").hidden = !S.sel.size; $("#selN").textContent = nf(S.sel.size); }
$("#selbar").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act]"); if (!a) return;
  const ids = [...S.sel]; const m = S.devMap || new Map();
  const copy = (f, what) => { const t = ids.map((i) => m.get(i)).filter(Boolean).map(f).filter(Boolean); navigator.clipboard.writeText(t.join("\n")).then(() => toast(`Скопировано ${what}: ${t.length}`)); };
  if (a.dataset.act === "clear") { S.sel.clear(); markSel(); }
  if (a.dataset.act === "list") { resetF(); S.f.ids = ids; go("asics", true); }
  if (a.dataset.act === "export") xlsx(ids);
  if (a.dataset.act === "copy") copy((d) => d.ip, "IP");
  if (a.dataset.act === "workers") copy((d) => d.worker, "воркеров");
  if (a.dataset.act === "compare") compare(ids);
});
async function compare(ids) {
  const devs = await loadDevices(); const m = new Map(devs.map((d) => [d.id, d]));
  const list = ids.map((i) => m.get(i)).filter(Boolean).slice(0, 500);
  const avg = (f) => { const v = list.map(f).filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  $("#mdIn").innerHTML = `<div class="dr-h"><h2>Сравнение · ${list.length} асиков</h2><span class="sp"></span><button class="btn" id="mdX">Закрыть</button></div>
   <div class="fbar"><span class="pill">средний хешрейт <b class="num">${nf(avg((d) => d.status === "working" ? d.ths : null), 1)} TH/s</b></span><span class="pill">средняя t° <b class="num">${nf(avg((d) => d.temp), 1)}</b></span><span class="pill">работают <b class="num">${list.filter((d) => d.status === "working").length}</b></span></div>
   <div class="scroll"><table class="tbl"><thead><tr><th>Модель</th><th>Место</th><th>IP</th><th>Статус</th><th class="r">TH/s</th><th class="r">% завод.</th><th class="r">t°</th><th class="r">Вт</th><th>Режим</th><th>Воркер</th><th>Ошибки</th></tr></thead><tbody>
   ${list.map((d) => `<tr data-id="${d.id}"><td class="lnk" data-card="${d.id}">${esc(d.model)}</td><td>${esc(placeTxt(d, true))}</td><td class="num">${esc(d.ip || "—")}</td><td>${stPill(d.status)}</td><td class="r num">${nf(d.ths, 1)}</td><td class="r num">${d.nominal && d.ths ? nf(d.ths / d.nominal * 100, 0) + "%" : "—"}</td><td class="r num">${nf(d.temp, 0)}</td><td class="r num">${nf(d.w, 0)}</td><td>${esc(modeRu(d.mode))}</td><td>${esc(d.worker || "")}</td><td>${d.alarms.map((a) => `<span class="lv${a.level}">${esc(a.msg)}</span>`).join(", ")}</td></tr>`).join("")}
   </tbody></table></div>`;
  $("#modal").hidden = false; $("#mdX").onclick = () => ($("#modal").hidden = true);
  $$("[data-card]", $("#mdIn")).forEach((x) => x.onclick = () => { $("#modal").hidden = true; openCard(+x.dataset.card); });
}

// ---------- asics ----------
const QUICK = { low: ["Low hashrate", isLow], hot: ["Горячие 80°+", isHot], noplace: ["Без места", (d) => d.container_id && !placeOf(d)] };
function resetF() { if (S.dt.tA) S.dt.tA.flt = {}; Object.assign(S.f, { q: "", status: "", cls: "", container: "", model: "", alarms: false, quick: "", ids: null }); }
async function pageAsics() {
  const seq = S.seq = (S.seq || 0) + 1;
  await loadSummary();
  const [devs] = await Promise.all([loadDevices(), loadLayouts(), loadCfg()]);
  const f = S.f; const q = f.q.trim().toLowerCase();
  const idset = f.ids ? new Set(f.ids) : null;
  const base = (d, skip) => (!idset || idset.has(d.id)) && (skip === "c" || !f.container || String(d.container_id) === f.container) && (skip === "m" || !f.model || d.model === f.model) && (!f.cls || d.alarms.some((a) => a.cls === f.cls))
    && (skip === "k" || !f.quick || QUICK[f.quick][1](d)) && (!q || [d.ip, d.mac, d.serial, d.worker, d.num, d.model, d.container, d.client].some((x) => x && String(x).toLowerCase().includes(q)));
  // Только варианты, где есть результат: счётчики по остальным фильтрам; невозможный выбор сбрасываем.
  const tally = (skip, keyFn) => { const o = {}; devs.forEach((d) => { if (base(d, skip)) { const k = keyFn(d); if (k != null && k !== "") o[k] = (o[k] || 0) + 1; } }); return o; };
  let cc = tally("c", (d) => d.container_id), mm = tally("m", (d) => d.model);
  if (f.container && !cc[f.container]) { f.container = ""; mm = tally("m", (d) => d.model); }
  if (f.model && !mm[f.model]) { f.model = ""; cc = tally("c", (d) => d.container_id); }
  const pre = devs.filter((d) => base(d));
  let list = pre.filter((d) => (!f.status || d.status === f.status) && (!f.alarms || d.alarms.length));
  const pk = (d) => { const p = placeOf(d); return p ? p.si * 1e6 + p.rack * 1e4 + p.shelf * 100 + p.num / 1000 : 9e9; };
  S.order = list.map((d) => d.id);
  const cnt = (fn) => pre.filter(fn).length;
  const models = Object.keys(mm).sort();
  const conts = filtConts().filter((c) => c.id && cc[c.id]).sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
  const show = list;
  const cname = f.container && (conts.find((c) => String(c.id) === f.container) || {}).name;
  const why = !list.length ? (pre.length ? `Под этот фильтр ничего: ${f.status ? `в выборке нет асиков со статусом «${ST[f.status]}»` : "нет асиков с ошибками"}${cname ? ` в контейнере «${cname}»` : ""}. Всего в выборке ${nf(pre.length)}: работают ${nf(cnt((d) => d.status === "working"))}, не работают ${nf(cnt((d) => d.status === "offline"))}.` : "Ничего не нашлось по поиску и фильтрам.") : "";
  if (S.page !== "asics" || seq !== S.seq) return;
  $("#main").innerHTML = `
   <div class="fbar"><input type="search" id="q" placeholder="IP, воркер, серийник, клиент…" value="${esc(f.q)}">
    <div class="chips">${[["", "Все", pre.length], ["working", "Работают", cnt((d) => d.status === "working")], ["offline", "Не работают", cnt((d) => d.status === "offline")], ["suspended", "Сон", cnt((d) => d.status === "suspended")]].map(([v, t, n]) => `<button class="chip ${f.status === v ? "on" : ""}" data-st="${v}" ${n || !v ? "" : "disabled"}>${t} <b>${nf(n)}</b></button>`).join("")}
     <button class="chip ${f.alarms ? "on" : ""}" id="onlyAl" ${cnt((d) => d.alarms.length) || f.alarms ? "" : "disabled"}>С ошибками <b>${nf(cnt((d) => d.alarms.length))}</b></button></div>
    <div class="chips">${Object.entries(QUICK).map(([k, [t, fn]]) => { const n = devs.filter((d) => base(d, "k") && fn(d)).length; return `<button class="chip q ${f.quick === k ? "on" : ""}" data-qk="${k}" ${n || f.quick === k ? "" : "disabled"}>${t} <b>${nf(n)}</b></button>`; }).join("")}</div>
    <select class="sel" id="fCont"><option value="">Все контейнеры · ${conts.length}</option>${conts.map((c) => `<option value="${c.id}" ${String(c.id) === f.container ? "selected" : ""}>${esc(c.name)} · ${nf(cc[c.id])}</option>`).join("")}</select>
    <select class="sel" id="fModel"><option value="">Все модели · ${models.length}</option>${models.map((m) => `<option value="${esc(m)}" ${m === f.model ? "selected" : ""}>${esc(m)} · ${nf(mm[m])}</option>`).join("")}</select>
    ${f.ids ? `<span class="pill">только выделенные ${nf(f.ids.length)} <button class="x" id="idsX" aria-label="убрать">✕</button></span>` : ""}
    ${f.q || f.status || f.container || f.model || f.alarms || f.quick || f.ids ? '<button class="btn ghost" id="fReset">Сбросить фильтры</button>' : ""}
    <span class="sp"></span><span class="hint" id="aN">${nf(list.length)} асиков</span><button class="btn" id="csv">Выгрузить в Excel</button></div>
   ${why ? `<div class="empty">${esc(why)}<br><button class="btn" id="fReset2" style="margin-top:10px">Сбросить статус</button></div>` : '<div class="w" id="dtA"></div>'}`;
  let qt; $("#q").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { f.q = e.target.value; pageAsics().then(() => { const i = $("#q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 250); };
  $$(".chip[data-st]").forEach((c) => c.onclick = () => { f.status = c.dataset.st; pageAsics(); });
  $$(".chip[data-qk]").forEach((c) => c.onclick = () => { f.quick = f.quick === c.dataset.qk ? "" : c.dataset.qk; pageAsics(); });
  $("#onlyAl").onclick = () => { f.alarms = !f.alarms; pageAsics(); };
  $("#fCont").onchange = (e) => { f.container = e.target.value; pageAsics(); };
  $("#fModel").onchange = (e) => { f.model = e.target.value; pageAsics(); };
  const r1 = $("#fReset"); if (r1) r1.onclick = () => { resetF(); pageAsics(); };
  const r2 = $("#fReset2"); if (r2) r2.onclick = () => { f.status = ""; f.alarms = false; pageAsics(); };
  const ix = $("#idsX"); if (ix) ix.onclick = () => { f.ids = null; pageAsics(); };
  let dt = null;
  if (list.length) dt = DT($("#dtA"), { id: "tA", title: "Асики", rows: list, sort: "place", cb: true, selId: (d) => d.id, rowAttr: (d) => `data-id="${d.id}"`, devIds: (r) => r.map((d) => d.id), cols: devCols(pk), onList: (l) => { S.order = l.map((d) => d.id); const h = $("#aN"); if (h) h.textContent = nf(l.length) + " асиков"; } });
  $("#csv").onclick = () => dt && dt.list.length ? xlsx(dt.list.map((d) => d.id)) : toast("Список пуст");
  $("#pageAside").innerHTML = list.length ? `<button class="btn" id="selAll">Выделить все в списке</button>` : "";
  const sa = $("#selAll"); if (sa) sa.onclick = () => { (dt ? dt.list : list).forEach((d) => S.sel.add(d.id)); markSel(); };
  if (dt) tableClicks("#tA");
  markSel();
}
const PAGE_N = 150;
// Endless list: rows are appended while the user scrolls, no "first N" cap.
function infinite(sel, arr, rowFn) {
  const tb = $(sel + " tbody"); if (!tb || arr.length <= PAGE_N) return;
  let n = PAGE_N;
  const tail = document.createElement("div"); tail.className = "more hint"; tail.textContent = `Показано ${nf(n)} из ${nf(arr.length)} · листайте вниз`;
  tb.closest("table").after(tail);
  const io = new IntersectionObserver((es) => {
    if (!es[0].isIntersecting || !tb.isConnected) return;
    const next = arr.slice(n, n + PAGE_N); n += next.length;
    tb.insertAdjacentHTML("beforeend", next.map(rowFn).join(""));
    markSel();
    if (n >= arr.length) { io.disconnect(); tail.textContent = `Все ${nf(arr.length)} в списке`; } else tail.textContent = `Показано ${nf(n)} из ${nf(arr.length)} · листайте вниз`;
  }, { root: tb.closest(".scroll"), rootMargin: "600px" });
  io.observe(tail);
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
async function xlsx(ids) {
  if (!ids.length) return toast("Ничего не выбрано");
  toast(`Готовлю Excel: ${nf(ids.length)} асиков…`);
  const t = tok.get();
  const r = await fetch(API + "/farm/devices/export.xlsx", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + (t && t.access_token) }, body: JSON.stringify({ ids }) });
  if (!r.ok) return toast("Не удалось выгрузить");
  saveBlob(await r.blob(), r.headers.get("Content-Disposition"));
}
function saveBlob(b, cd) { const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = ((cd || "").split('filename="')[1] || "kirka.xlsx").replace(/"/g, ""); document.body.appendChild(a); a.click(); a.remove(); }

// ---------- problems ----------
async function pageErrors() {
  const seq = S.seq = (S.seq || 0) + 1;
  await loadSummary();
  await Promise.all([loadLayouts(), loadDevices(), loadCfg()]);
  const ef = S.ef; const eq = ef.q.trim().toLowerCase();
  // Карточки классов — по выбранным площадке и клиенту, считаем асики; пустые не показываем.
  const clsN = {}; let anyAl = 0;
  S.devs.forEach((d) => { if (!d.alarms.length) return; anyAl++; new Set(d.alarms.map((a) => a.cls)).forEach((k) => (clsN[k] = (clsN[k] || 0) + 1)); });
  if (ef.cls && !clsN[ef.cls]) { ef.cls = ""; }
  const okE = (d, skip) => d.alarms.some((a) => (!ef.cls || a.cls === ef.cls) && (!ef.level || (a.level || 0) >= +ef.level))
    && (!eq || [d.ip, d.mac, d.serial, d.worker, d.num, d.model].some((x) => x && String(x).toLowerCase().includes(eq)))
    && (skip === "c" || !ef.container || String(d.container_id) === ef.container) && (skip === "m" || !ef.model || d.model === ef.model);
  const tallyE = (skip, keyFn) => { const o = {}; S.devs.forEach((d) => { if (okE(d, skip)) { const k = keyFn(d); if (k != null && k !== "") o[k] = (o[k] || 0) + 1; } }); return o; };
  let ecc = tallyE("c", (d) => d.container_id), emm = tallyE("m", (d) => d.model);
  if (ef.container && !ecc[ef.container]) { ef.container = ""; emm = tallyE("m", (d) => d.model); }
  if (ef.model && !emm[ef.model]) { ef.model = ""; ecc = tallyE("c", (d) => d.container_id); }
  // Список строим из того же снимка асиков, что и счётчики в меню, — числа всегда совпадают.
  const clsL = Object.fromEntries(S.sum.classes.map((c) => [c.key, c.label]));
  const eItems = [];
  S.devs.forEach((d) => { if (!okE(d)) return; d.alarms.forEach((a) => { if ((ef.cls && a.cls !== ef.cls) || (ef.level && (a.level || 0) < +ef.level)) return;
    eItems.push({ id: d.id, model: d.model, site: d.site, container: d.container, client: d.client, ip: d.ip, worker: d.worker, serial: d.serial, status: d.status, temp: d.temp,
      code: a.code, msg: a.msg, cls: a.cls, cls_label: clsL[a.cls || "other"] || "Прочее", lvl: a.level, since: a.since }); }); });
  const r = { items: eItems, devices: new Set(eItems.map((x) => x.id)).size };
  const conts = filtConts().filter((c) => c.id && ecc[c.id]).sort((a, b) => a.name.localeCompare(b.name, "ru", { numeric: true }));
  const models = Object.keys(emm).sort();
  // groups computed here: error → devices, top containers, oldest
  const gm = {};
  r.items.forEach((x) => { const k = x.code + " · " + x.msg; const g = gm[k] = gm[k] || { k, code: x.code, msg: x.msg, cls: x.cls_label, lvl: x.lvl, ids: new Set(), conts: {}, since: null }; g.ids.add(x.id); g.conts[x.container || "—"] = (g.conts[x.container || "—"] || 0) + 1; if (x.since && (!g.since || x.since < g.since)) g.since = x.since; });
  const groups = Object.values(gm).sort((a, b) => b.lvl - a.lvl || b.ids.size - a.ids.size);
  const items = ef.gk ? r.items.filter((x) => x.code + " · " + x.msg === ef.gk) : r.items;
  const show = items;
  const rowE = (x) => { const d = S.devMap && S.devMap.get(x.id); return `<tr data-id="${x.id}"><td class="cb"><input type="checkbox" aria-label="выбрать"></td><td><span class="lv${x.lvl}">${esc(x.msg)}</span> <span class="faint num">${esc(x.code)}</span></td><td class="lv${x.lvl}">${LV[x.lvl] || "—"}</td><td class="num">${tfmt(x.since)}</td><td>${esc(x.model || "")}</td><td title="${esc(x.container)}"><span class="cn">${esc((x.container || "—").split(" ").slice(0, 2).join(" "))}</span> <span class="faint">${d ? esc(placeTxt(d, true)) : ""}</span></td><td class="num">${esc(x.ip || "—")}</td><td>${esc(x.client || "—")}</td><td class="r num">${nf(x.temp, 0)}</td></tr>`; };
  S.order = [...new Set(show.map((x) => x.id))];
  const all = S.sum.classes.map((c) => ({ ...c, count: clsN[c.key] || 0 })).filter((c) => c.count);
  if (S.page !== "errors" || seq !== S.seq) return;
  $("#main").innerHTML = `
  <div class="cls-row"><button class="clsc ${!ef.cls ? "on" : ""}" data-cls=""><span>Все проблемы</span><b class="num">${nf(anyAl)}</b><i>асиков</i></button>${all.map((c) => `<button class="clsc ${ef.cls === c.key ? "on" : ""} c-${c.key}" data-cls="${c.key}"><span>${esc(c.label)}</span><b class="num">${nf(c.count)}</b><i>асиков</i></button>`).join("")}</div>
  <div class="fbar"><input type="search" id="eq" placeholder="IP, воркер, серийник…" value="${esc(ef.q)}">
   <select class="sel" id="eCont"><option value="">Все контейнеры · ${conts.length}</option>${conts.map((c) => `<option value="${c.id}" ${String(c.id) === ef.container ? "selected" : ""}>${esc(c.name)} · ${nf(ecc[c.id])}</option>`).join("")}</select>
   <select class="sel" id="eModel"><option value="">Все модели · ${models.length}</option>${models.map((m) => `<option value="${esc(m)}" ${m === ef.model ? "selected" : ""}>${esc(m)} · ${nf(emm[m])}</option>`).join("")}</select>
   <select class="sel" id="eLvl"><option value="">Любая важность</option><option value="1" ${ef.level === "1" ? "selected" : ""}>от предупреждения</option><option value="2" ${ef.level === "2" ? "selected" : ""}>от «опасно»</option><option value="3" ${ef.level === "3" ? "selected" : ""}>только критичные</option></select>
   <span class="sp"></span><button class="btn pri" id="eXls">Выгрузить в Excel</button></div>
  <div class="w"><div class="wh">Какие ошибки · ${groups.length}<span class="sp"></span><span class="mini">клик по строке — показать асики с этой ошибкой ниже</span></div><div class="scroll" style="max-height:360px"><table class="tbl" id="tG"><thead><tr><th>Ошибка</th><th>Код</th><th>Класс</th><th>Важность</th><th class="r">Асиков</th><th>Где больше всего</th><th>Самая давняя</th></tr></thead><tbody>
   ${groups.map((g) => { const top = Object.entries(g.conts).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([n, c]) => `${esc(n.split(" ").slice(0, 2).join(" "))} <span class="faint">${c}</span>`).join(", "); return `<tr data-gk="${esc(g.k)}" class="${ef.gk === g.k ? "on" : ""}"><td class="lnk"><span class="lv${g.lvl}">${esc(g.msg)}</span></td><td class="num faint">${esc(g.code)}</td><td>${esc(g.cls)}</td><td class="lv${g.lvl}">${LV[g.lvl] || "—"}</td><td class="r num"><b>${nf(g.ids.size)}</b></td><td>${top}</td><td class="num">${tfmt(g.since)}</td></tr>`; }).join("") || '<tr><td colspan="7"><div class="empty">Ошибок нет</div></td></tr>'}
  </tbody></table></div></div>
  <div class="w" style="margin-top:10px"><div class="wh">${ef.gk ? `Асики с ошибкой «${esc(ef.gk.split(" · ").slice(1).join(" · "))}» · ${nf(new Set(items.map((x) => x.id)).size)} <button class="btn ghost" id="gkX" style="height:22px;margin-left:6px">показать все</button>` : `Все асики с ошибками · ${nf(r.devices)}`}<span class="sp"></span><button class="btn" id="eSel" style="height:24px">Выделить эти асики</button></div><div class="scroll"><table class="tbl" id="tE"><thead><tr><th class="cb"><input type="checkbox" id="ecbAll" aria-label="Выделить все"></th><th>Ошибка</th><th>Важность</th><th>С какого времени</th><th>Модель</th><th>Контейнер и место</th><th>IP</th><th>Клиент</th><th class="r">t°</th></tr></thead><tbody>
    ${show.slice(0, PAGE_N).map(rowE).join("")}
    </tbody></table></div></div>`;
  $("#pageAside").innerHTML = `<label class="mini lowset" title="Асик попадает в Low hashrate, если его хешрейт в среднем за час ниже этого процента от заводского">Low hashrate ниже <input type="number" id="lowPct" min="30" max="99" value="${lowPct()}">% заводского</label>`;
  $("#lowPct").onchange = async (e) => { const v = Math.round(+e.target.value); if (!(v >= 30 && v <= 99)) { e.target.value = lowPct(); return toast("Порог — от 30 до 99%"); } try { S.cfg = await api("/farm/settings", { method: "PUT", body: JSON.stringify({ lowhash_pct: v }) }); toast(`Порог Low hashrate: ${v}%. Пересчёт — со следующим обновлением`); } catch (err) { toast("Не удалось сохранить"); } };
  let qt; $("#eq").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { ef.q = e.target.value; pageErrors().then(() => { const i = $("#eq"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  $$(".clsc").forEach((c) => c.onclick = () => { ef.cls = c.dataset.cls; ef.gk = ""; pageErrors(); });
  $("#eCont").onchange = (e) => { ef.container = e.target.value; pageErrors(); };
  $("#eModel").onchange = (e) => { ef.model = e.target.value; pageErrors(); };
  $("#eLvl").onchange = (e) => { ef.level = e.target.value; pageErrors(); };
  $$("#tG tr[data-gk]").forEach((row) => row.onclick = () => { ef.gk = ef.gk === row.dataset.gk ? "" : row.dataset.gk; pageErrors().then(() => $("#tE") && $("#tE").closest(".w").scrollIntoView({ behavior: "smooth", block: "start" })); });
  const gx = $("#gkX"); if (gx) gx.onclick = () => { ef.gk = ""; pageErrors(); };
  $("#eSel").onclick = () => { items.forEach((x) => S.sel.add(x.id)); markSel(); };
  $("#ecbAll").onchange = (e) => { show.forEach((x) => e.target.checked ? S.sel.add(x.id) : S.sel.delete(x.id)); markSel(); };
  $("#eXls").onclick = () => S.sel.size ? xlsx([...S.sel]) : ef.gk ? xlsx([...new Set(items.map((x) => x.id))]) : download("/farm/errors/export.xlsx?" + qs({ site: site(), client: client(), cls: ef.cls, container: ef.container, model: ef.model }));
  tableClicks("#tE");
  infinite("#tE", show, rowE);
  markSel();
}

// ---------- clients ----------
async function pageClients() {
  const seq = S.seq = (S.seq || 0) + 1;
  await loadSummary();
  const devs = await loadDevices();
  const m = {};
  devs.forEach((d) => { const k = m[d.client_id || 0] = m[d.client_id || 0] || { id: d.client_id, name: d.client || "Без клиента", total: 0, working: 0, offline: 0, susp: 0, ths: 0, nom: 0, kw: 0, al: 0, conts: new Set(), models: {} }; k.total++; if (d.status === "working") { k.working++; k.ths += d.ths || 0; k.nom += d.nominal || 0; } if (d.status === "offline") k.offline++; if (d.status === "suspended") k.susp++; k.kw += (d.w || 0) / 1000; if (d.alarms.length) k.al++; if (d.container_id) k.conts.add(d.container_id); k.models[d.model] = (k.models[d.model] || 0) + 1; });
  const cl = Object.values(m).sort((a, b) => b.total - a.total);
  if (S.page !== "clients" || seq !== S.seq) return;
  $("#main").innerHTML = `<div class="w"><div class="wh">Клиенты · ${cl.length}<span class="sp"></span><span class="mini">«В работе» — доля асиков клиента, которые майнят прямо сейчас. «% от заводского» — их хешрейт к паспортному</span></div><div class="scroll"><table class="tbl" id="tCl"><thead><tr><th>Клиент</th><th class="r">Асиков</th><th class="r">В работе</th><th class="r">Не работают</th><th class="r">С ошибками</th><th class="r">PH/s</th><th class="r">% от заводского</th><th class="r">кВт</th><th class="r">Контейнеров</th><th>Модели</th></tr></thead><tbody>
   ${cl.map((c) => { const p = c.total ? c.working / c.total * 100 : 0; return `<tr data-client="${c.id || ""}"><td class="lnk">${esc((c.name || "").trim())}</td><td class="r num">${nf(c.total)}</td><td class="r"><span class="pct ${p >= 95 ? "p-ok" : p >= 80 ? "p-warn" : "p-bad"}">${nf(p, 0)}%</span></td><td class="r num ${c.offline ? "bad" : "faint"}">${nf(c.offline)}</td><td class="r num ${c.al ? "warn" : "faint"}">${nf(c.al)}</td><td class="r num">${nf(c.ths / 1000, 2)}</td><td class="r num">${c.nom ? nf(c.ths / c.nom * 100, 0) + "%" : "—"}</td><td class="r num">${nf(c.kw, 0)}</td><td class="r num">${c.conts.size}</td><td class="small">${Object.entries(c.models).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([n, k]) => `${esc(n || "—")} <span class="faint">${k}</span>`).join(", ")}</td></tr>`; }).join("")}
   </tbody></table></div></div>`;
  $$("#tCl tr[data-client]").forEach((x) => x.onclick = () => { setClient(x.dataset.client); S.devs = null; go("today"); });
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
   <div class="dr-h"><div><h2>${esc(d.model)} ${d.num ? `<span class="faint">№ ${esc(d.num)}</span>` : ""}</h2><div class="mut small">${esc((d.site || "").trim())} · ${d.container_id ? `<a href="#cont/${d.container_id}" class="crumb-a" id="drCont">${esc(d.container || "")}</a>` : "без контейнера"}</div><div class="small"><b>${esc(placeTxt(d))}</b></div></div><span class="sp"></span>${stPill(d.status)}<button class="btn" id="drX">Закрыть</button></div>
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
   ${d.pool_worker ? `<div class="sec"><h4>Этот воркер в пуле</h4><dl class="kv"><dt>Статус в пуле</dt><dd>${d.pool_worker.status === "active" ? '<span class="ok">в работе</span>' : '<span class="bad">нет хешрейта</span>'}</dd><dt>10 мин / 1 ч / 24 ч</dt><dd class="num">${nf(d.pool_worker.ths_10m, 1)} / ${nf(d.pool_worker.ths_1h, 1)} / ${nf(d.pool_worker.ths_1d, 1)} TH/s${d.ths && d.pool_worker.ths_1d ? ` <span class="${d.pool_worker.ths_1d < d.ths * 0.9 ? "warn" : "faint"}">· пул ${nf(d.pool_worker.ths_1d / d.ths * 100, 0)}% от асика</span>` : ""}</dd><dt>Отказы за час</dt><dd class="num">${d.pool_worker.reject_1h != null ? nf(d.pool_worker.reject_1h * 100, 2) + "%" : "—"}</dd><dt>Онлайн за 7 дней</dt><dd class="num">${d.pool_worker.online_7d_h != null ? nf(d.pool_worker.online_7d_h, 0) + " ч из 168" : "—"}</dd></dl></div>` : ""}
   <div class="sec"><h4>Ошибки сейчас</h4>${d.alarms.length ? `<ul class="tl">${d.alarms.map((a) => `<li><span class="d">${tfmt(a.since)}</span><span class="dot" style="background:var(--bad)"></span><span><span class="lv${a.level}">${esc(a.msg)}</span> <span class="faint">${esc(a.code)} · ${CLS[a.cls] || ""} · ${LV[a.level] || ""}</span></span></li>`).join("")}</ul>` : '<div class="hint">Ошибок нет</div>'}</div>
   <div class="sec"><h4>Хешрейт по часам, 72 ч</h4>${d.hourly.length > 1 ? lineChart(d.hourly.map((h) => [new Date(h.h).getTime(), h.ths]), "TH/s") : '<div class="hint">История копится с момента подключения Kirka.</div>'}</div>
   <div class="sec"><h4>История болезни</h4>${d.events.length ? `<ul class="tl">${d.events.map((e) => `<li><span class="d">${tfmt(e.at)}</span><span class="dot" style="background:${evCol[e.kind] || "var(--mut)"}"></span><span><b>${evName[e.kind] || e.kind}</b> · ${evTxt(e)}</span></li>`).join("")}</ul>` : '<div class="hint">Пока событий нет</div>'}</div>`;
  $("#drX").onclick = () => ($("#drawer").hidden = true);
  const dc = $("#drCont"); if (dc) dc.onclick = (e) => { e.preventDefault(); $("#drawer").hidden = true; go("cont", false, d.container_id); };
}
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("#drawer").hidden = true; });
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#drawer").hidden = true; $("#modal").hidden = true; } if ((e.ctrlKey || e.metaKey) && e.key === "a" && ["cont", "asics", "errors"].includes(S.page) && !e.target.matches("input")) { e.preventDefault(); S.order.forEach((i) => S.sel.add(i)); markSel(); } });

// ---------- common ----------
// ---------- pools ----------
const mskToday = () => new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
const addDays = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * 86400e3).toISOString().slice(0, 10);
S.pf = { from: null, to: null, group: "", q: "", empty: false };
function fh(ths) { if (ths == null) return "—"; if (ths === 0) return "0"; if (ths >= 1e6) return nf(ths / 1e6, 2) + " EH"; if (ths >= 1000) return nf(ths / 1000, 2) + " PH"; if (ths >= 1) return nf(ths, 1) + " TH"; return nf(ths * 1000, 0) + " GH"; }
const dm = (iso) => iso.slice(8, 10) + "." + iso.slice(5, 7);
async function pagePools() {
  const pf = S.pf; pf.to = pf.to || mskToday(); pf.from = pf.from || addDays(pf.to, -6);
  const [acc, daily] = await Promise.all([api("/pools/accounts"), api("/pools/daily?" + qs({ d_from: pf.from, d_to: pf.to, group: pf.group }))]);
  const groups = [...new Set(acc.items.map((a) => a.group || "Без группы"))];
  const q = pf.q.toLowerCase();
  const alive = (it) => (it.account.ths_1d || 0) > 0 || (it.account.ths_10m || 0) > 0 || it.days.some((d) => d.ths);
  const found = daily.items.filter((it) => !q || [it.account.account, it.account.user, it.account.group].some((x) => x && x.toLowerCase().includes(q)));
  const hidden = found.filter((it) => !alive(it)).length;
  const gi = {}; found.forEach((it, i) => { const g = it.account.group || ""; if (!(g in gi)) gi[g] = i; });
  const items = found.filter((it) => pf.empty || alive(it)).sort((x, y) => gi[x.account.group || ""] - gi[y.account.group || ""] || (y.account.ths_1d || 0) - (x.account.ths_1d || 0));
  const byCoin = {}; found.forEach((it) => { const c = byCoin[it.account.coin] = byCoin[it.account.coin] || { h10: 0, h24: 0, n: 0 }; c.h10 += it.account.ths_10m || 0; c.h24 += it.account.ths_1d || 0; if (alive(it)) c.n++; });
  const coinLine = (k) => Object.entries(byCoin).filter(([, v]) => v[k] > 0).sort((a, b) => b[1][k] - a[1][k]).map(([c, v]) => `<span class="num">${esc(c)} ${fh(v[k])}</span>`).join(" · ") || "—";
  const days = items.length ? items[0].days.map((d) => d.day) : [];
  const tot = items.reduce((a, it) => { a.h10 += it.account.ths_10m || 0; a.h24 += it.account.ths_1d || 0; a.wa += it.account.w_active || 0; a.wi += it.account.w_inactive || 0; return a; }, { h10: 0, h24: 0, wa: 0, wi: 0 });
  const errs = acc.items.filter((a) => a.error).length;
  let lastG = null;
  const rowsHtml = items.map((it) => {
    const a = it.account; const g = a.group || "Без группы"; let head = "";
    if (g !== lastG) { lastG = g; head = `<tr class="grp"><td colspan="${7 + days.length}">${esc(g)}</td></tr>`; }
    const vals = it.days.map((d) => d.ths); const ok = vals.filter((v) => v != null);
    const avg = ok.length ? ok.reduce((x, y) => x + y, 0) / ok.length : null;
    const prof = it.days.reduce((x, d) => x + (d.profit || 0), 0);
    const down = a.ths_10m != null && a.ths_1d && a.ths_10m < a.ths_1d * 0.7;
    return head + `<tr data-pacc="${a.id}"><td class="lnk">${esc(a.account || a.user || "—")}${a.user && a.account && a.user.toLowerCase() !== a.account.toLowerCase() ? ` <span class="faint">${esc(a.user)}</span>` : ""}${a.error ? ` <span class="bad" title="${esc(a.error)}">●</span>` : ""}${a.note ? ` <span class="faint" title="${esc(a.note)}">ⓘ</span>` : ""}</td><td>${esc(a.coin)}</td>
      <td class="r num ${down ? "bad" : ""}">${fh(a.ths_10m)}</td><td class="r num">${fh(a.ths_1d)}</td><td class="r num"><span class="ok">${a.w_active ?? "—"}</span>${a.w_inactive ? ` / <span class="bad">${a.w_inactive}</span>` : ""}</td>
      ${it.days.map((d) => `<td class="r num ${d.part ? "warn" : ""}" title="${esc(d.src || "")}">${d.ths == null ? '<span class="faint">—</span>' : fh(d.ths)}</td>`).join("")}
      <td class="r num"><b>${fh(avg)}</b></td><td class="r num">${prof ? nf(prof, 8) : "—"}</td></tr>`;
  }).join("");
  if (S.page !== "pools") return;
  $("#main").innerHTML = `
  <div class="kpis" style="grid-template-columns:repeat(4,1fr)">
   <div class="k"><div class="l">Ссылок пулов</div><div class="v">${nf(acc.items.length)}</div><div class="s">${errs ? `<span class="bad">${errs} не отвечают</span>` : "все отвечают"} · других пулов ${acc.other.length}</div></div>
   <div class="k"><div class="l">Хешрейт сейчас, 10 мин</div><div class="v" style="font-size:14px;white-space:normal;overflow:visible;line-height:1.4">${coinLine("h10")}</div><div class="s">по монетам, H/s</div></div>
   <div class="k"><div class="l">Хешрейт за 24 ч</div><div class="v" style="font-size:14px;white-space:normal;overflow:visible;line-height:1.4">${coinLine("h24")}</div><div class="s">ссылок с хешрейтом ${nf(found.filter(alive).length)}</div></div>
   <div class="k"><div class="l">Воркеры в пуле</div><div class="v"><span class="ok">${nf(tot.wa)}</span></div><div class="s">неактивны ${nf(tot.wi)}</div></div>
  </div>
  <div class="fbar" style="margin-top:10px"><input type="search" id="pq" placeholder="Аккаунт, группа…" value="${esc(pf.q)}">
   <select class="sel" id="pGrp"><option value="">Все группы</option>${groups.map((g) => `<option ${g === pf.group ? "selected" : ""}>${esc(g)}</option>`).join("")}</select>
   <label class="mini">с <input type="date" id="pFrom" class="sel" value="${pf.from}"></label><label class="mini">по <input type="date" id="pTo" class="sel" value="${pf.to}"></label>
   <span class="seg" id="pQuick"><button data-n="7">7 дн</button><button data-n="30">30 дн</button><button data-n="m">месяц</button></span>
   <label class="mini"><input type="checkbox" id="pEmpty" ${pf.empty ? "checked" : ""}> пустые (${hidden})</label>
   <span class="sp"></span><button class="btn" id="pImp">Загрузить таблицу</button><button class="btn pri" id="pXls">Выгрузить Excel</button></div>
  <div class="w" style="margin-top:10px"><div class="wh">Средний хешрейт по суткам МСК (00:00–24:00)<span class="sp"></span><span class="mini">Kirka сама копит часы — пул хранит только 72 ч. Жёлтым — сутки собраны не полностью. Наведите на число — как посчитано.</span></div>
   <div class="scroll"><table class="tbl" id="tP"><thead><tr><th>Аккаунт</th><th>Монета</th><th class="r">10 мин</th><th class="r">24 ч</th><th class="r">Воркеры</th>${days.map((d) => `<th class="r">${dm(d)}</th>`).join("")}<th class="r">Среднее</th><th class="r">Начислено</th></tr></thead>
   <tbody>${rowsHtml || `<tr><td colspan="${7 + days.length}"><div class="empty">Ссылок пулов пока нет. Нажмите «Загрузить таблицу» и выберите файл Mining Monitor.</div></td></tr>`}</tbody></table></div></div>
  ${acc.other.length ? `<div class="w" style="margin-top:10px"><div class="wh">Другие пулы · ${acc.other.length}<span class="sp"></span><span class="mini">Ссылки сохранены, чтение подключаю по очереди</span></div><div class="wb"><div class="hl">${acc.other.map((o) => `<div class="row"><span class="nm">${esc(o.group || "")} · <b>${esc(o.provider)}</b> <span class="faint">${esc((o.url || "").replace(/^https?:\/\//, "").slice(0, 70))}</span></span><span class="mini">ждёт</span></div>`).join("")}</div></div></div>` : ""}`;
  let qt; $("#pq").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { pf.q = e.target.value; pagePools().then(() => { const i = $("#pq"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
  $("#pGrp").onchange = (e) => { pf.group = e.target.value; pagePools(); };
  $("#pEmpty").onchange = (e) => { pf.empty = e.target.checked; pagePools(); };
  $("#pFrom").onchange = (e) => { pf.from = e.target.value; pagePools(); };
  $("#pTo").onchange = (e) => { pf.to = e.target.value; pagePools(); };
  $$("#pQuick button").forEach((b) => b.onclick = () => { const t = mskToday(); pf.to = t; pf.from = b.dataset.n === "m" ? t.slice(0, 8) + "01" : addDays(t, -(+b.dataset.n - 1)); pagePools(); });
  $("#pXls").onclick = () => download("/pools/daily/export.xlsx?" + qs({ d_from: pf.from, d_to: pf.to, group: pf.group }));
  $("#pImp").onclick = () => $("#poolFile").click();
  $$("#tP tr[data-pacc]").forEach((r) => r.onclick = () => openPool(+r.dataset.pacc));
}
$("#poolFile").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return; e.target.value = "";
  toast("Читаю таблицу и проверяю ссылки…");
  const t = tok.get(); const fd = new FormData(); fd.append("file", f);
  const r = await fetch(API + "/pools/import", { method: "POST", headers: { Authorization: "Bearer " + (t && t.access_token) }, body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) return toast(j.detail || "Не получилось загрузить");
  toast(`Добавлено ${j.added}, обновлено ${j.updated}, нерабочих ссылок ${j.invalid}, других пулов ${j.other_links}`);
  go("pools", true);
};
async function openPool(id) {
  $("#drIn").innerHTML = '<div class="spin" style="margin:40px auto"></div>'; $("#drawer").hidden = false;
  const pf = S.pf;
  const [d, w] = await Promise.all([api(`/pools/accounts/${id}/daily?` + qs({ d_from: pf.from, d_to: pf.to })), api(`/pools/accounts/${id}/workers`)]);
  const a = d.account;
  const pts = d.rows.filter((r) => r.avg_ths != null).map((r) => [Date.parse(r.day + "T00:00:00Z"), r.avg_ths]);
  const wsorted = w.items.slice().sort((x, y) => (x.status === "active") - (y.status === "active") || (x.ths_1h || 0) - (y.ths_1h || 0));
  $("#drIn").innerHTML = `
   <div class="dr-h"><div><h2>${esc(a.account || a.user)} <span class="faint">${esc(a.coin)}</span></h2><div class="mut small">${esc(a.group || "")} · ViaBTC</div></div><span class="sp"></span><button class="btn" id="drX">Закрыть</button></div>
   <div class="life">
    <div class="k"><div class="l">Сейчас, 10 мин</div><div class="v">${fh(a.ths_10m)}<small>/s</small></div><div class="s">за час ${fh(a.ths_1h)}</div></div>
    <div class="k"><div class="l">Воркеры</div><div class="v"><span class="ok">${a.w_active ?? "—"}</span></div><div class="s">неактивны ${a.w_inactive ?? 0} · ошибочные ${a.w_invalid ?? 0}</div></div>
    <div class="k"><div class="l">Начислено за 24 ч</div><div class="v">${a.profit_24h != null ? nf(a.profit_24h, 8) : "—"}</div><div class="s">баланс ${a.balance != null ? nf(a.balance, 8) : "—"} ${esc(a.coin)}</div></div>
   </div>
   ${a.error ? `<div class="sec"><div class="bad">Пул не отвечает: ${esc(a.error)}</div></div>` : ""}${a.note ? `<div class="sec"><div class="hint">По этой ссылке пул не показывает: ${esc(a.note)}</div></div>` : ""}
   <div class="sec"><h4>Средний хешрейт по суткам МСК</h4>${pts.length > 1 ? lineChart(pts, "TH/s") : ""}
    <table class="tbl"><thead><tr><th>Сутки</th><th class="r">Среднее</th><th class="r">Часов</th><th>Как посчитано</th><th class="r">Начислено</th><th class="r">≈ $</th></tr></thead><tbody>
    ${d.rows.map((r) => `<tr><td class="num">${dm(r.day)}</td><td class="r num">${fh(r.avg_ths)}</td><td class="r num">${r.hours}</td><td class="mini">${esc(r.source)}</td><td class="r num">${r.profit != null ? nf(r.profit, 8) : "—"}</td><td class="r num">${r.money_usd != null ? nf(r.money_usd, 2) : "—"}</td></tr>`).join("")}
    <tr><td><b>Итого</b></td><td class="r num"><b>${fh(d.total.avg_ths)}</b></td><td></td><td class="mini">${d.total.days} сут. с данными</td><td class="r num"><b>${d.total.profit ? nf(d.total.profit, 8) : "—"}</b></td><td class="r num"><b>${d.total.money_usd ? nf(d.total.money_usd, 2) : "—"}</b></td></tr>
    </tbody></table><div class="hint" style="margin-top:6px">Архив Kirka: ${nf(d.archive_hours)} ч${d.archive_since ? " с " + tfmt(d.archive_since) : ""}. Начисления — по суткам пула.</div></div>
   <div class="sec"><h4>Воркеры · ${w.items.length} · найдено в CROFT ${w.matched}</h4>
    <table class="tbl"><thead><tr><th>Воркер</th><th>Статус</th><th class="r">1 ч</th><th class="r">24 ч</th><th class="r">Отказ</th><th>Асик в CROFT</th></tr></thead><tbody>
    ${wsorted.slice(0, 400).map((x) => `<tr ${x.device ? `data-card="${x.device.id}" class="lnk"` : ""}><td class="num">${esc(x.name)}</td><td>${x.status === "active" ? '<span class="ok">в работе</span>' : `<span class="bad">${x.status === "unactive" ? "нет хешрейта" : esc(x.status || "—")}</span>`}</td><td class="r num">${fh(x.ths_1h)}</td><td class="r num">${fh(x.ths_1d)}</td><td class="r num">${x.reject_1h != null ? nf(x.reject_1h * 100, 2) + "%" : "—"}</td><td>${x.device ? `${esc(x.device.model || "")} <span class="faint num">${esc(x.device.ip || "")}</span> ${stPill(x.device.status)}` : '<span class="faint">—</span>'}</td></tr>`).join("")}
    </tbody></table></div>`;
  $("#drX").onclick = () => ($("#drawer").hidden = true);
  $$("#drIn tr[data-card]").forEach((r) => r.onclick = () => openCard(+r.dataset.card));
}

async function download(path) {
  const t = tok.get(); const r = await fetch(API + path, { headers: { Authorization: "Bearer " + (t && t.access_token) } });
  if (!r.ok) return toast("Не удалось выгрузить");
  saveBlob(await r.blob(), r.headers.get("Content-Disposition"));
}
function toast(t) { const el = $("#toast"); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (el.hidden = true), 2200); }

// ---------- общая таблица: сортировка по любому столбцу, фильтр под каждым столбцом, выгрузка ----------
// cols: { k, t, r (по правому краю), v(row) — значение для сортировки, h(row) — html ячейки,
//         f: "text" | "sel" | "num" | null, fv(row) — значение для фильтра, fl(value) — подпись в списке }
S.dt = S.dt || {};
const ipNum = (ip) => (ip || "").split(".").reduce((a, x) => a * 256 + (+x || 0), 0);
function numMatch(expr, v) {
  const e = String(expr).replace(/\s/g, "").replace(",", ".");
  if (!e) return true; if (v == null || isNaN(v)) return false;
  let m;
  if ((m = e.match(/^(<=|>=|<|>|=)?(-?\d+(?:\.\d+)?)$/))) { const n = +m[2]; return { "<": v < n, ">": v > n, "<=": v <= n, ">=": v >= n, "=": v === n }[m[1] || ">="]; }
  if ((m = e.match(/^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$/))) return v >= +m[1] && v <= +m[2];
  return true;
}
function dtState(id, def) { return S.dt[id] = S.dt[id] || { sort: def || "", flt: {} }; }
function dtApply(rows, cols, st, skipK) {
  return rows.filter((r) => cols.every((c) => {
    const fv = st.flt[c.k]; if (!fv || c.k === skipK) return true;
    const v = c.fv ? c.fv(r) : c.v(r);
    if (c.f === "num") return numMatch(fv, v);
    if (c.f === "sel") return String(v ?? "—") === fv;
    return String(v ?? "").toLowerCase().includes(String(fv).toLowerCase());
  }));
}
function dtSort(rows, cols, st) {
  const k = st.sort.replace("-", ""), desc = st.sort[0] === "-"; const c = cols.find((x) => x.k === k); if (!c) return rows;
  return rows.slice().sort((a, b) => { const x = c.v(a), y = c.v(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "ru", { numeric: true })) * (desc ? -1 : 1); });
}
// Рисует таблицу в host. cfg: { id, cols, rows, rowAttr(r), sort, title, devIds(rows), maxH, cb (галочки выделения), foot }
function DT(host, cfg) {
  const st = dtState(cfg.id, cfg.sort); const cols = cfg.cols;
  const filtered = () => dtSort(dtApply(cfg.rows, cols, st), cols, st);
  let list = filtered();
  const selOpts = (c) => { const o = {}; dtApply(cfg.rows, cols, st, c.k).forEach((r) => { const v = String((c.fv ? c.fv(r) : c.v(r)) ?? "—"); o[v] = (o[v] || 0) + 1; }); return Object.entries(o).sort((a, b) => a[0].localeCompare(b[0], "ru", { numeric: true })); };
  const fcell = (c) => {
    if (!c.f) return "<th></th>";
    if (c.f === "sel") { const opts = selOpts(c); return `<th><select class="dtf" data-k="${c.k}"><option value="">все</option>${opts.map(([v, n]) => `<option value="${esc(v)}" ${st.flt[c.k] === v ? "selected" : ""}>${esc(c.fl ? c.fl(v) : v)} · ${nf(n)}</option>`).join("")}</select></th>`; }
    return `<th><input class="dtf" data-k="${c.k}" value="${esc(st.flt[c.k] || "")}" placeholder="фильтр" ${c.f === "num" ? `title="Число: placeholder="${c.f === "num" ? "&gt;90, &lt;50, 10-20" : "фильтр"}"gt;90, placeholder="${c.f === "num" ? "&gt;90, &lt;50, 10-20" : "фильтр"}"lt;50 или диапазон 10-20"` : ""}></th>`;
  };
  const head = () => {
    const k = st.sort.replace("-", ""), desc = st.sort[0] === "-";
    return `<tr>${cfg.cb ? '<th class="cb"><input type="checkbox" data-all aria-label="Выделить все"></th>' : ""}${cols.map((c) => `<th class="${c.r ? "r" : ""} srt" data-sort="${c.k}" title="Сортировать" ${c.w ? `style="width:${c.w}"` : ""}>${c.t}<i class="ar">${k === c.k ? (desc ? "↓" : "↑") : "↕"}</i></th>`).join("")}</tr>
      <tr class="frow">${cfg.cb ? "<th></th>" : ""}${cols.map(fcell).join("")}</tr>`;
  };
  const row = (r) => `<tr ${cfg.rowAttr ? cfg.rowAttr(r) : ""}>${cfg.cb ? '<td class="cb"><input type="checkbox" aria-label="выбрать"></td>' : ""}${cols.map((c) => `<td class="${c.r ? "r num" : ""} ${c.cls || ""}">${c.h ? c.h(r) : esc(c.v(r) ?? "—")}</td>`).join("")}</tr>`;
  const anyF = () => Object.values(st.flt).some(Boolean);
  host.innerHTML = `<div class="dt"><div class="scroll" ${cfg.maxH ? `style="max-height:${cfg.maxH}"` : ""}><table class="tbl" id="${cfg.id}"><thead>${head()}</thead><tbody></tbody></table><div class="more hint" hidden></div></div>
    <div class="dt-foot"><span class="hint dtn"></span><button class="btn ghost sm dtx" hidden>Сбросить фильтры</button><span class="sp"></span>${cfg.foot || ""}<button class="btn sm dtxl">Скачать Excel</button><button class="btn sm dtpdf">Скачать PDF</button></div></div>`;
  const tb = $("tbody", host), tail = $(".more", host);
  let n = 0;
  const more = () => { const next = list.slice(n, n + PAGE_N); n += next.length; tb.insertAdjacentHTML("beforeend", next.map(row).join("")); tail.hidden = n >= list.length; tail.textContent = `Показано ${nf(n)} из ${nf(list.length)} · листайте вниз`; if (cfg.cb) markSel(); };
  const body = () => {
    list = filtered(); tb.innerHTML = ""; n = 0; more();
    if (!list.length) tb.innerHTML = `<tr><td colspan="${cols.length + (cfg.cb ? 1 : 0)}"><div class="empty">Под фильтры ничего не подходит</div></td></tr>`;
    $(".dtn", host).textContent = anyF() ? `${nf(list.length)} из ${nf(cfg.rows.length)}` : `${nf(list.length)} в списке`;
    $(".dtx", host).hidden = !anyF();
    if (cfg.onList) cfg.onList(list);
  };
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting && tb.isConnected && n < list.length) more(); }, { root: $(".scroll", host), rootMargin: "600px" });
  io.observe(tail);
  const rehead = () => { $("thead", host).innerHTML = head(); bindHead(); };
  const bindHead = () => {
    $$("th[data-sort]", host).forEach((h) => h.onclick = () => { const k = h.dataset.sort; st.sort = st.sort === k ? "-" + k : st.sort === "-" + k ? k : k; rehead(); body(); });
    $$(".dtf", host).forEach((inp) => {
      inp.onclick = (e) => e.stopPropagation();
      const upd = () => { st.flt[inp.dataset.k] = inp.value.trim(); body(); $$("select.dtf", host).forEach((s) => { if (s !== inp) { const c = cols.find((x) => x.k === s.dataset.k); const cur = st.flt[c.k] || ""; s.innerHTML = `<option value="">все</option>${selOpts(c).map(([v, nn]) => `<option value="${esc(v)}" ${cur === v ? "selected" : ""}>${esc(c.fl ? c.fl(v) : v)} · ${nf(nn)}</option>`).join("")}`; } }); };
      if (inp.tagName === "SELECT") inp.onchange = upd; else { let t; inp.oninput = () => { clearTimeout(t); t = setTimeout(upd, 200); }; }
    });
    const all = $("[data-all]", host); if (all) all.onchange = (e) => { list.forEach((r) => { const id = cfg.selId(r); e.target.checked ? S.sel.add(id) : S.sel.delete(id); }); markSel(); };
  };
  bindHead();
  $(".dtx", host).onclick = () => { st.flt = {}; rehead(); body(); };
  $(".dtxl", host).onclick = () => { if (!list.length) return toast("Список пуст"); if (cfg.devIds) return xlsx(cfg.devIds(list)); csvSave(cfg.title || "kirka", cols, list); };
  $(".dtpdf", host).onclick = () => { if (!list.length) return toast("Список пуст"); printTable(cfg.title || "Kirka", cfg.pcols || cols, list); };
  body();
  return { get list() { return list; }, refresh: body };
}
// Текст ячейки без разметки — через DOM, а не регулярками.
const txtOf = (html) => { const t = document.createElement("template"); t.innerHTML = String(html ?? ""); return t.content.textContent.replace(/\s+/g, " ").trim(); };
const plainCell = (c, r) => { const v = c.p ? c.p(r) : c.h ? txtOf(c.h(r)) : c.v(r); return v == null ? "" : String(v); };
function csvSave(name, cols, rows) {
  const q = (s) => /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  const txt = [cols.map((c) => q(txtOf(c.t))).join(";"), ...rows.map((r) => cols.map((c) => q(plainCell(c, r))).join(";"))].join("\r\n");
  const stamp = new Date().toLocaleString("sv-SE", MSK).slice(0, 16).replace(/[-: ]/g, "").replace(/^(\d{8})/, "$1_");
  saveBlob(new Blob(["\ufeff" + txt], { type: "text/csv;charset=utf-8" }), `filename="${name}_${stamp}.csv"`);
}
// PDF — через печать браузера: чистая таблица на белом, «Сохранить как PDF».
function printTable(title, cols, rows) {
  const w = window.open("", "_blank"); if (!w) return toast("Разрешите всплывающие окна для PDF");
  const when = new Date().toLocaleString("ru-RU", MSK);
  w.document.write(`<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>${esc(title)}</title><style>
   body{font:11px/1.35 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111;margin:18px}h1{font-size:16px;margin:0 0 2px}.m{color:#666;margin-bottom:10px}
   table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:3px 6px;text-align:left;vertical-align:top}th{background:#f2f4f5;font-weight:600}
   tr:nth-child(even) td{background:#fafafa}@page{size:A4 landscape;margin:10mm}</style></head><body>
   <h1>${esc(title)}</h1><div class="m">Kirka · ${esc(when)} МСК · строк: ${rows.length}</div>
   <table><thead><tr>${cols.map((c) => `<th>${esc(txtOf(c.t))}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(plainCell(c, r))}</td>`).join("")}</tr>`).join("")}</tbody></table>
   <script>onload=()=>setTimeout(()=>print(),200)<\/script></body></html>`);
  w.document.close();
}

// ---------- выбор периода для графиков: сутки 00:00–00:00 МСК ----------
const mskDay = (shift = 0) => { const d = new Date(Date.now() + 3 * 3600e3); d.setUTCDate(d.getUTCDate() + shift); return d.toISOString().slice(0, 10); };
function periodPresets() {
  const t = mskDay(0), y = mskDay(-1);
  const d = new Date(Date.now() + 3 * 3600e3); const dow = (d.getUTCDay() + 6) % 7;
  const m0 = t.slice(0, 8) + "01";
  const pm = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1)).toISOString().slice(0, 10), pmEnd = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 0)).toISOString().slice(0, 10);
  return [["Сегодня", t, t], ["Вчера", y, y], ["Эта неделя", mskDay(-dow), t], ["Этот месяц", m0, t], ["Прошлый месяц", pm, pmEnd]];
}
const dRu = (iso) => iso.slice(8, 10) + "." + iso.slice(5, 7);
// Кнопка «Период»: popover с готовыми периодами и выбором дат. onPick({start,end,label}).
function periodBtn(btn, cur, onPick) {
  btn.onclick = (e) => {
    e.stopPropagation();
    let pop = $(".perpop"); if (pop) { pop.remove(); return; }
    pop = document.createElement("div"); pop.className = "perpop";
    const t = mskDay(0);
    pop.innerHTML = `<div class="pp-l">${periodPresets().map(([l, a, b]) => `<button class="pp-i ${cur && cur.label === l ? "on" : ""}" data-a="${a}" data-b="${b}">${l}<span class="faint">${a === b ? dRu(a) : dRu(a) + "–" + dRu(b)}</span></button>`).join("")}</div>
      <div class="pp-d"><label>с <input type="date" id="ppA" max="${t}" value="${cur && cur.start || mskDay(-6)}"></label><label>по <input type="date" id="ppB" max="${t}" value="${cur && cur.end || t}"></label><button class="btn pri sm" id="ppGo">Показать</button></div>
      <div class="hint">Сутки — с 00:00 до 00:00 по Москве</div>`;
    document.body.appendChild(pop);
    const r = btn.getBoundingClientRect(); pop.style.top = (r.bottom + scrollY + 6) + "px"; pop.style.left = Math.max(8, Math.min(r.left + scrollX, innerWidth - 300)) + "px";
    pop.onclick = (ev) => ev.stopPropagation();
    $$(".pp-i", pop).forEach((b) => b.onclick = () => { pop.remove(); onPick({ start: b.dataset.a, end: b.dataset.b, label: b.firstChild.textContent }); });
    $("#ppGo", pop).onclick = () => { let a = $("#ppA", pop).value, b = $("#ppB", pop).value; if (!a || !b) return; if (a > b) [a, b] = [b, a]; pop.remove(); onPick({ start: a, end: b, label: a === b ? dRu(a) : dRu(a) + "–" + dRu(b) }); };
  };
}
document.addEventListener("click", () => { const p = $(".perpop"); if (p) p.remove(); });
const periodHours = (ch) => ch.start ? Math.round((new Date(ch.end + "T00:00:00+03:00") - new Date(ch.start + "T00:00:00+03:00")) / 3.6e6) + 24 : ch.h;

// ---------- настройки фермы (порог Low hashrate и др.) ----------
async function loadCfg(force) { if (!S.cfg || force) { try { S.cfg = await api("/farm/settings"); } catch (e) { S.cfg = { lowhash_pct: 90 }; } } return S.cfg; }

// Столбцы списка асиков — одинаковые на странице «Асики» и в контейнере.
function devCols(pk, o = {}) {
  const pct = (d) => d.status === "working" && d.nominal && d.ths != null ? d.ths / d.nominal * 100 : null;
  const cols = [
    { k: "place", t: o.noCont ? "Место" : "Контейнер и место", v: (d) => o.noCont ? pk(d) : (d.container || "") + "|" + String(pk(d)).padStart(14, "0"), fv: (d) => (o.noCont ? "" : (d.container || "") + " ") + (placeOf(d) ? placeTxt(d, true) : "без места"),
      h: (d) => o.noCont ? (placeOf(d) ? esc(placeTxt(d, true)) : '<span class="warn">без места</span>') : `<span class="cn" title="${esc(d.container)}">${esc((d.container || "—").split(" ").slice(0, 2).join(" "))}</span> <span class="faint">${placeOf(d) ? esc(placeTxt(d, true)) : "без места"}</span>`, p: (d) => (o.noCont ? "" : (d.container || "") + " ") + (placeOf(d) ? placeTxt(d, true) : "без места"), f: "text" },
    { k: "model", t: "Модель", v: (d) => d.model, h: (d) => `<span class="lnk">${esc(d.model || "—")}</span>`, f: "sel" },
    { k: "status", t: "Статус", v: (d) => d.status, h: (d) => stPill(d.status), p: (d) => ST[d.status] || d.status, f: "sel", fl: (v) => ST[v] || v },
    { k: "ths", t: "TH/s", r: 1, v: (d) => d.status === "working" ? d.ths : null, h: (d) => d.status === "working" ? nf(d.ths, 1) + (isLow(d) ? ` <span class="warn" title="Low hashrate">↓</span>` : "") : "—", p: (d) => d.status === "working" ? nf(d.ths, 1) : "", f: "num" },
    { k: "pct", t: "% завода", r: 1, v: pct, h: (d) => { const p = pct(d); return p == null ? "—" : `<span class="${p < lowPct() ? "warn" : ""}">${nf(p, 0)}%</span>`; }, f: "num" },
    { k: "temp", t: "t°", r: 1, v: (d) => d.temp, h: (d) => `<span class="${d.temp >= 80 ? "warn" : ""}">${nf(d.temp, 0)}</span>`, f: "num" },
    { k: "w", t: "Вт", r: 1, v: (d) => d.w, h: (d) => nf(d.w, 0), f: "num" },
    { k: "ip", t: "IP", v: (d) => ipNum(d.ip), fv: (d) => d.ip || "", h: (d) => `<span class="num">${esc(d.ip || "—")}</span>`, p: (d) => d.ip || "", f: "text" },
    { k: "client", t: "Клиент", v: (d) => d.client || "—", f: "sel" },
    { k: "worker", t: "Воркер", v: (d) => d.worker, h: (d) => `<span class="num" title="${esc(d.worker)}">${esc(d.worker || "—")}</span>`, f: "text" },
    { k: "uptime", t: "Без перезагрузки", r: 1, v: (d) => d.status === "working" ? d.uptime : null, h: (d) => d.status === "working" ? dur(d.uptime) : "—", p: (d) => d.status === "working" ? dur(d.uptime) : "", f: null },
    { k: "alarms", t: "Ошибки", v: (d) => d.alarms.length, fv: (d) => d.alarms.map((a) => a.msg + " " + a.code).join(" "), h: (d) => d.alarms.map((a) => `<span class="lv${a.level}" title="${esc(a.code)}">${esc(a.msg)}</span>`).join(", "), p: (d) => d.alarms.map((a) => a.msg).join("; "), f: "text" }];
  return o.noCont ? cols : cols;
}

// ---------- калькулятор доходности (любой алгоритм, цена в ₽ или USDT) ----------
const CALC = { models: null, f: { q: "", id: null, qty: 1, price: "5.5", cur: "RUB", fee: "1", up: "100", hw: "" } };
async function apiJ(path, body) {
  const r = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let j = null; try { j = await r.json(); } catch (e) { j = null; }
  if (!r.ok) { const d = j && j.detail; throw new Error(typeof d === "string" ? d : "Ошибка расчёта (" + r.status + ")"); }
  return j;
}
const HRU = (m) => m.hashrate_unit || "TH/s";
const mName = (m) => `${m.brand} ${m.model_name}`;
async function pageCalc() {
  if (!CALC.models) {
    const all = []; for (let skip = 0; skip < 2000; skip += 200) { const part = await api("/devices/models?limit=200&skip=" + skip); all.push(...part); if (part.length < 200) break; }
    CALC.models = all.filter((m) => m.is_active !== false).sort((a, b) => mName(a).localeCompare(mName(b), "ru"));
  }
  const f = CALC.f;
  const algos = [...new Set(CALC.models.map((m) => m.algorithm || "SHA-256"))].sort();
  $("#main").innerHTML = `
  <div class="grid">
    <div class="w c5"><div class="wh">Оборудование и условия</div><div class="wb">
      <div class="cform">
        <label class="lbl">Модель</label>
        <div class="crow"><select id="cAlgo" class="in" style="max-width:150px"><option value="">Все алгоритмы</option>${algos.map((a) => `<option ${f.algo === a ? "selected" : ""}>${esc(a)}</option>`).join("")}</select>
        <input id="cQ" class="in" placeholder="Поиск: S21, L9, KS5…" value="${esc(f.q)}" autocomplete="off"></div>
        <select id="cModel" class="in" size="8" style="height:auto"></select>
        <div class="hint" id="cSpec">Выберите модель из каталога</div>
        <div class="c2">
          <div><label class="lbl">Количество</label><input id="cQty" class="in" type="number" min="1" max="100000" value="${esc(f.qty)}"></div>
          <div><label class="lbl">Цена кВт·ч</label><div class="crow"><input id="cPrice" class="in" inputmode="decimal" value="${esc(f.price)}"><span class="seg" id="cCur"><button data-c="RUB" class="${f.cur === "RUB" ? "on" : ""}">₽</button><button data-c="USDT" class="${f.cur === "USDT" ? "on" : ""}">USDT</button></span></div></div>
          <div><label class="lbl">Комиссия пула, %</label><input id="cFee" class="in" inputmode="decimal" value="${esc(f.fee)}"></div>
          <div><label class="lbl">Аптайм, %</label><input id="cUp" class="in" inputmode="decimal" value="${esc(f.up)}"></div>
          <div class="c2s"><label class="lbl">Стоимость оборудования, ${f.cur === "RUB" ? "₽" : "USDT"} (для окупаемости)</label><input id="cHw" class="in" inputmode="decimal" placeholder="необязательно" value="${esc(f.hw)}"></div>
        </div>
        <button class="btn pri wide" id="cGo">Рассчитать</button>
      </div>
    </div></div>
    <div class="w c7"><div class="wh">Результат<span class="sp"></span><span class="mini" id="cAt"></span></div><div class="wb" id="cRes"><div class="empty">Выберите модель и нажмите «Рассчитать»</div></div></div>
  </div>`;
  const fill = () => {
    const q = f.q.trim().toLowerCase();
    const list = CALC.models.filter((m) => (!f.algo || (m.algorithm || "SHA-256") === f.algo) && (!q || mName(m).toLowerCase().includes(q)));
    $("#cModel").innerHTML = list.slice(0, 300).map((m) => `<option value="${m.id}" ${m.id === f.id ? "selected" : ""}>${esc(mName(m))} · ${nf(m.default_hashrate_ths, 0)} ${esc(HRU(m))} · ${nf(m.default_power_w)} Вт</option>`).join("") || "<option disabled>Ничего не найдено</option>";
    spec();
  };
  const spec = () => { const m = CALC.models.find((x) => x.id === f.id); $("#cSpec").textContent = m ? `${m.algorithm || "SHA-256"}${m.coin_family ? " · " + m.coin_family : ""} · ${nf(m.default_hashrate_ths, 1)} ${HRU(m)} · ${nf(m.default_power_w)} Вт` : "Выберите модель из каталога"; };
  fill();
  $("#cQ").oninput = (e) => { f.q = e.target.value; fill(); };
  $("#cAlgo").onchange = (e) => { f.algo = e.target.value; fill(); };
  $("#cModel").onchange = (e) => { f.id = +e.target.value; spec(); };
  $("#cModel").ondblclick = () => run();
  $$("#cCur button").forEach((b) => b.onclick = () => { f.cur = b.dataset.c; if (f.cur === "USDT" && +String(f.price).replace(",", ".") > 1) f.price = "0.06"; if (f.cur === "RUB" && +String(f.price).replace(",", ".") < 1) f.price = "5.5"; keep(); pageCalc(); });
  const keep = () => { f.qty = $("#cQty").value; f.price = $("#cPrice").value; f.fee = $("#cFee").value; f.up = $("#cUp").value; f.hw = $("#cHw").value; };
  const run = async () => {
    keep();
    if (!f.id) return toast("Выберите модель");
    $("#cRes").innerHTML = '<div class="empty">Считаю…</div>';
    try {
      const r = await apiJ("/calc/model", { device_model_id: f.id, quantity: +f.qty || 1, power_price: f.price, power_price_currency: f.cur, pool_fee_pct: f.fee, uptime_pct: f.up, hardware_cost: f.hw || null });
      calcResult(r);
      if (innerWidth < 900) $("#cRes").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) { $("#cRes").innerHTML = `<div class="empty">⚠️ ${esc(e.message)}</div>`; }
  };
  $("#cGo").onclick = run;
  $$("#main .cform input").forEach((i) => i.onkeydown = (e) => { if (e.key === "Enter") run(); });
  if (CALC.last && CALC.last.input && f.id) calcResult(CALC.last);
}
function calcResult(r) {
  CALC.last = r;
  const L = r.local || {}, rub = r.currency !== "USDT", rate = +r.rate || 1;
  const m2 = (loc, usd, d = 2) => rub ? `<b class="num">${nf(loc, d)} ₽</b> <span class="faint num">${nf(usd, d)} USDT</span>` : `<b class="num">${nf(usd, d)} USDT</b>`;
  const net = +r.net_profit_day;
  const mined = (r.coins && r.coins.length ? r.coins.map((c) => `${nf(+c.per_day, +c.per_day >= 100 ? 2 : 6)} ${esc(c.coin)}`) : [`${nf(+r.btc_per_day, 8)} BTC`]).join(" + ");
  const roi = r.roi_days != null ? `${nf(r.roi_days, 0)} дн. (~${nf(r.roi_days / 30.4, 1)} мес.)` : "укажите стоимость оборудования";
  const be = r.break_even_power_price != null ? (rub ? `${nf(L.break_even_power_price, 2)} ₽ / ${nf(r.break_even_power_price, 4)} USDT` : `${nf(r.break_even_power_price, 4)} USDT`) : "—";
  $("#cAt").textContent = "рынок: " + tfmt(r.market_captured_at);
  $("#cRes").innerHTML = `
    <div class="kpis">
      <div class="k"><div class="lbl">Чистая прибыль / сутки</div><div class="num" style="font-size:20px;color:var(${net >= 0 ? "--ok" : "--bad"})">${rub ? nf(L.net_profit_day, 0) + " ₽" : nf(net, 2) + " USDT"}</div></div>
      <div class="k"><div class="lbl">В месяц</div><div class="num" style="font-size:20px">${rub ? nf(L.net_profit_month, 0) + " ₽" : nf(r.net_profit_month, 2) + " USDT"}</div></div>
      <div class="k"><div class="lbl">Окупаемость</div><div class="num" style="font-size:16px">${roi}</div></div>
    </div>
    <dl class="kv">
      <dt>Алгоритм</dt><dd>${esc(r.algorithm || "SHA-256")}</dd>
      <dt>Добыча / сутки</dt><dd class="num">${mined}</dd>
      <dt>Доход / сутки</dt><dd>${m2(L.pool_revenue_usdt_day, r.pool_revenue_usdt_day)} <span class="faint">после комиссии пула</span></dd>
      <dt>Электроэнергия</dt><dd>${m2(L.power_cost_day, r.power_cost_day)} / сутки · ${nf(r.input.power_w * r.input.quantity * 24 / 1000, 1)} кВт·ч</dd>
      <dt>Прибыль / год</dt><dd>${m2(L.net_profit_year, r.net_profit_year, 0)}</dd>
      <dt>Безубыточно до</dt><dd>${be} за кВт·ч</dd>
      ${rub ? `<dt>Курс</dt><dd class="num">1 USDT = ${nf(rate, 2)} ₽</dd>` : ""}
    </dl>
    <div class="hint" style="margin-top:10px">Расчёт по текущей сложности сети и курсу, без учёта будущих изменений.</div>`;
}

// ---------- уведомления в Telegram ----------
const TIMES = Array.from({ length: 48 }, (_, i) => String(Math.floor(i / 2)).padStart(2, "0") + ":" + (i % 2 ? "30" : "00"));
const tsel = (id, cur) => `<select class="in" id="${id}" style="width:96px">${(TIMES.includes(cur) ? TIMES : [cur, ...TIMES]).map((t) => `<option ${t === cur ? "selected" : ""}>${t}</option>`).join("")}</select>`;
const NT = { HOLD: [0, 5, 10, 15, 30, 60], EVERY: [5, 15, 30, 60, 180], WAKE: [0, 10, 30, 100] };
async function pageNotify() {
  const d = await api("/farm/notify");
  const p = d.prefs, T = d.types, Lv = d.levels;
  const opt = (arr, cur, fmt) => arr.map((v) => `<option value="${v}" ${v === cur ? "selected" : ""}>${fmt(v)}</option>`).join("");
  $("#main").innerHTML = `
  <div class="grid">
    <div class="w c7"><div class="wh">Уведомления в Telegram<span class="sp"></span><label class="tgl"><input type="checkbox" id="nOn" ${p.enabled ? "checked" : ""}> ${p.enabled ? "включены" : "выключены"}</label></div><div class="wb">
      ${d.linked ? "" : `<div class="okbox" style="margin-bottom:10px">Аккаунт не связан с Telegram — войдите на сайт через QR из бота, чтобы получать уведомления.</div>`}
      <div class="sec" style="margin-top:0"><h4>Что присылать</h4><div class="ntypes">${Object.entries(T).map(([k, v]) => `<label class="tgl"><input type="checkbox" data-t="${k}" ${p.types[k] ? "checked" : ""}> ${esc(v)}</label>`).join("")}</div></div>
      <div class="sec"><h4>Фильтр от «мигания»</h4><div class="c2">
        <div><label class="lbl">Ошибки</label><select class="in" id="nLvl">${opt([0, 1, 2, 3], p.min_level, (v) => Lv[v])}</select></div>
        <div><label class="lbl">Сообщать, если держится</label><select class="in" id="nHold">${opt(NT.HOLD, p.hold_min, (v) => v ? `≥ ${v} мин` : "сразу")}</select></div>
        <div><label class="lbl">Сводка изменений</label><select class="in" id="nEvery">${opt(NT.EVERY, p.every_min, (v) => `раз в ${v} мин`)}</select></div>
        <div><label class="lbl">Список асиков в сообщении</label><select class="in" id="nMax">${opt([0, 5, 10, 20], p.max_lines, (v) => v ? `до ${v}` : "только итоги")}</select></div>
      </div></div>
      <div class="sec"><h4>Сон</h4><div class="crow"><label class="tgl"><input type="checkbox" id="nSl" ${p.sleep.on ? "checked" : ""}> не беспокоить</label>
        <span>с</span>${tsel("nFrom", p.sleep.from)}<span>до</span>${tsel("nTo", p.sleep.to)}</div>
        <div class="crow" style="margin-top:8px"><span class="mut">Будить во сне при отключении</span><select class="in" id="nWake" style="width:160px;flex:none">${opt(NT.WAKE, p.wake_offline, (v) => v ? `≥ ${v} асиков` : "не будить")}</select></div>
        <div class="hint">Во сне изменения копятся и приходят одним сообщением после пробуждения.</div></div>
      <div class="sec"><h4>Утренняя сводка</h4><div class="crow"><span class="mut">Время (МСК)</span>${tsel("nDaily", p.daily_at)}</div></div>
      <div class="crow" style="margin-top:14px"><button class="btn pri" id="nSave">Сохранить</button><button class="btn" id="nTest" ${d.linked ? "" : "disabled"}>Прислать проверку</button><span class="hint" id="nMsg"></span></div>
    </div></div>
    <div class="w c5"><div class="wh">Как это работает</div><div class="wb"><div class="hint" style="font-size:12.5px;line-height:1.55">
      Бот не пересылает каждое событие CROFT — за сутки их тысячи. Раз в выбранный интервал он сравнивает состояние фермы с тем, о чём уже сообщил, и присылает одно сообщение: сколько асиков пропало и вернулось, какие ошибки появились, где больше всего (по контейнерам).<br><br>
      Ошибка или «нет связи» попадает в сообщение, только если держится дольше порога — короткое «мигание» не тревожит.<br><br>
      Эти же настройки есть в боте: кнопка «🔔 Уведомления» в меню или команда /notify.</div></div></div>
  </div>`;
  $("#nOn").onchange = () => save();
  const collect = () => {
    const types = {}; $$("[data-t]").forEach((i) => types[i.dataset.t] = i.checked);
    return { enabled: $("#nOn").checked, types, min_level: +$("#nLvl").value, hold_min: +$("#nHold").value, every_min: +$("#nEvery").value, max_lines: +$("#nMax").value,
      sleep: { on: $("#nSl").checked, from: $("#nFrom").value || "23:00", to: $("#nTo").value || "08:00" }, wake_offline: +$("#nWake").value, daily_at: $("#nDaily").value || "09:00" };
  };
  const save = async () => {
    try { await api("/farm/notify", { method: "PUT", body: JSON.stringify(collect()) }); toast("Уведомления сохранены"); pageNotify(); }
    catch (e) { toast(e.message === "HTTP 400" ? "Сначала свяжите аккаунт с Telegram" : "Не удалось сохранить"); }
  };
  $("#nSave").onclick = save;
  $("#nTest").onclick = async () => { try { await api("/farm/notify", { method: "PUT", body: JSON.stringify(collect()) }); const r = await api("/farm/notify/test", { method: "POST" }); toast(r.sent ? "Проверка отправлена в Telegram" : "Не удалось отправить"); } catch (e) { toast("Не удалось отправить"); } };
}
$$("[data-go]").forEach((b) => b.onclick = () => go(b.dataset.go));

// ---------- Энергетика: схема питания (живые данные CROFT), 3D, почасовки, счётчики ----------
// Топология Диагонали — реестр счётчиков 26.10 + схема расстановки (подтверждено).
const EN_FEED = [
  { id: 0, n: "Ф-1", full: "ГПП-2 · Ф-1 · РП №2", site: "Диагональ", meter: "49049738-23", color: "#2F7FD0", ktp: [1, 2, 3, 4, 5] },
  { id: 1, n: "Ф-24", full: "РП №2 · Ф-24", site: "Hydro Диагональ", meter: "49049736-23", color: "#D97A1E", ktp: [6, 7, 8, 9, 10] },
];
const EN_KTPM = { 1: "49603054-24", 2: "49602919-24", 3: "49603111-24", 4: "49603081-24", 5: "49716752-24", 6: "49602900-24", 7: "49615046-24", 8: "49558811-24", 9: "49615266-24", 10: "49558813-24" };
const EN_CAP = 2500; // кВА на КТП
// Контейнер → КТП по вводам (ключ — первое слово имени в CROFT).
const EN_CT = { "1а": [1], "2к": [1], "3а": [2], "4к": [2], "5а": [3], "6к": [3], "7К": [4], "8к": [4], "9К": [5], "10к": [5], "11К": [3], "А": [2, 3, 3], "Б": [1, 4, 4],
  "11к": [7], "12к": [7], "13к": [6], "14к": [6], "15к": [10], "16к": [8], "17к": [8], "18к": [9], "19к": [9], "20к": [10] };
const EN = { sub: "scheme", feed: "", hg: "day", hr: null };
function enKtpOf(name) {
  const key = String(name || "").trim().split(/\s+/)[0];
  if (EN_CT[key]) return EN_CT[key];
  if (/^модуль/i.test(name)) { const m = /КТП\s*(\d+)/i.exec(name); if (m) return [+m[1]]; }
  return null;
}
function enModel(sum) {
  const ktp = {}; for (let k = 1; k <= 10; k++) ktp[k] = { k, kw: 0, items: [] };
  const other = [];
  for (const c of sum.containers || []) {
    const ks = enKtpOf(c.name);
    if (!ks) { other.push(c); continue; }
    const share = 1 / ks.length;
    const cnt = {}; ks.forEach((k) => cnt[k] = (cnt[k] || 0) + 1);
    Object.entries(cnt).forEach(([k, n]) => { ktp[k].kw += (c.kw || 0) * share * n; ktp[k].items.push({ c, part: ks.length > 1 ? `${n} из ${ks.length} вводов` : "", kw: (c.kw || 0) * share * n }); });
  }
  const feeds = EN_FEED.map((f) => ({ ...f, kw: f.ktp.reduce((s, k) => s + ktp[k].kw, 0), cont: new Set(f.ktp.flatMap((k) => ktp[k].items.map((i) => i.c.id))).size }));
  return { ktp, feeds, other };
}
const enShort = (n) => { const s = String(n || ""); const m = /^(\S+)/.exec(s); return /^модуль/i.test(s) ? s.replace(/\s*\(Распределительный пункт\)/, "").replace(/\s*\(КТП[^)]*\)/, "") : (m ? m[1] : s); };
// Пользователь мог переключить вкладку, пока грузились данные: устаревший ответ не перерисовывает страницу.
const enStale = (q) => q !== EN.seq || S.page !== "energy";
async function pageEnergy() {
  const sub = EN.sub; EN.seq = (EN.seq || 0) + 1;
  $("#pageAside").innerHTML = `<span class="seg" id="enSub">${[["scheme", "Схема питания"], ["3d", "3D площадка"], ["hourly", "Почасовки"], ["meters", "Счётчики"]].map(([k, v]) => `<button data-s="${k}" class="${sub === k ? "on" : ""}">${v}</button>`).join("")}</span>`;
  $$("#enSub button").forEach((b) => b.onclick = () => { EN.sub = b.dataset.s; go("energy", true); });
  if (sub === "3d") return enPage3d();
  if (sub === "hourly") return enPageHourly();
  if (sub === "meters") return enPageMeters();
  return enPageScheme();
}
async function enPageScheme() {
  const q = EN.seq;
  const sum = await api("/farm/summary");
  if (enStale(q)) return;
  const M = enModel(sum);
  const tot = M.feeds.reduce((s, f) => s + f.kw, 0);
  const pct = (kw) => Math.round(kw / EN_CAP * 100);
  const lc = (p) => p >= 90 ? "var(--bad)" : p >= 75 ? "var(--warn)" : "var(--ok)";
  const stc = (c) => c.total && c.working === 0 ? "var(--faint)" : c.offline / Math.max(1, c.total) > 0.1 ? "var(--warn)" : "var(--ok)";
  const chip = (it) => { const c = it.c; return `<button class="enc" data-cid="${c.id}" title="${esc(c.name)}"><i style="background:${stc(c)}"></i><b>${esc(enShort(c.name))}</b><span class="num">${nf(it.kw, 0)} кВт</span><span class="faint num">${nf(c.working)}/${nf(c.total)}</span>${it.part ? `<em>${esc(it.part)}</em>` : ""}</button>`; };
  const feedCard = (f) => `
    <div class="w c6 enf" style="--fc:${f.color}"><div class="wh"><span class="enfl"></span><b>${esc(f.n)}</b>&nbsp;<span class="mut">${esc(f.full)} · ${esc(f.site)}</span><span class="sp"></span><span class="num"><b>${nf(f.kw / 1000, 2)}</b> МВт</span></div>
    <div class="wb"><div class="hint" style="margin-bottom:8px">Счётчик фидера ${esc(f.meter)} · ${nf(f.cont)} контейнеров · КТП ${f.ktp[0]}–${f.ktp[f.ktp.length - 1]}</div>
    ${f.ktp.map((k) => { const K = M.ktp[k], p = pct(K.kw); return `
      <div class="enk"><div class="enkh"><b>КТП ${k}</b><span class="faint">сч. ${EN_KTPM[k]}</span><span class="sp"></span><span class="num">${nf(K.kw, 0)} кВт</span><span class="enbar"><i style="width:${Math.min(100, p)}%;background:${lc(p)}"></i></span><span class="num" style="width:38px;text-align:right;color:${lc(p)}">${p}%</span></div>
      <div class="encs">${K.items.length ? K.items.sort((a, b) => b.kw - a.kw).map(chip).join("") : '<span class="hint">нет контейнеров</span>'}</div></div>`; }).join("")}
    </div></div>`;
  $("#main").innerHTML = `
    <div class="kpis">
      <div class="k"><div class="lbl">Расчёт по асикам · сейчас</div><div class="num" style="font-size:20px">${nf(tot / 1000, 2)} МВт</div><div class="hint">сумма потребления асиков CROFT</div></div>
      ${M.feeds.map((f) => `<div class="k"><div class="lbl"><i class="dot" style="background:${f.color}"></i> ${esc(f.n)} · ${esc(f.site)}</div><div class="num" style="font-size:20px">${nf(f.kw / 1000, 2)} МВт</div><div class="hint">${f.ktp.length} КТП по ${EN_CAP} кВА</div></div>`).join("")}
      <div class="k"><div class="lbl">Самая загруженная КТП</div>${(() => { const k = Object.values(M.ktp).sort((a, b) => b.kw - a.kw)[0]; const p = pct(k.kw); return `<div class="num" style="font-size:20px;color:${lc(p)}">КТП ${k.k} · ${p}%</div><div class="hint">${nf(k.kw, 0)} из ${EN_CAP} кВА</div>`; })()}</div>
    </div>
    <div class="grid">${M.feeds.map(feedCard).join("")}</div>
    ${M.other.length ? `<div class="hint" style="margin-top:10px">Без привязки к КТП: ${M.other.map((c) => esc(c.name)).join(", ")}</div>` : ""}
    <div class="hint" style="margin-top:10px">Нагрузка — по асикам из CROFT, обновляется раз в минуту. Контейнеры с несколькими вводами делятся по вводам (А: ввод 1 — КТП 2, вводы 2–3 — КТП 3; Б: ввод 1 — КТП 1, вводы 2–3 — КТП 4). Клик по контейнеру — карта контейнера.</div>`;
  $$("#main .enc").forEach((b) => b.onclick = () => go("cont", false, b.dataset.cid));
}
async function enPage3d() {
  const q = EN.seq;
  await api("/farm/summary"); // обновит токен: 3D читает живые данные тем же входом
  if (enStale(q)) return;
  $("#main").innerHTML = `<div class="w" style="height:calc(100vh - 150px);min-height:520px;overflow:hidden"><iframe id="en3d" src="/demo/energy3d.html?embed=1&live=1&v=5" style="border:0;width:100%;height:100%;display:block" title="3D площадка"></iframe></div>`;
}
async function enPageHourly() {
  const q = EN.seq;
  const [meters, h] = await Promise.all([api("/energy/meters"), api("/energy/hourly?group=" + EN.hg)]);
  if (enStale(q)) return;
  const S2 = h.series || [];
  const max = Math.max(1, ...S2.map((s) => s.kwh));
  const W = Math.max(320, Math.round(($("#main").clientWidth || 1000) - 60)), Hh = 220, bw = W / Math.max(1, S2.length);
  const cols = ["#2F7FD0", "#D97A1E", "#3FA96B", "#9B6BD6"];
  const ids = meters.map((m) => String(m.id));
  // По суткам — столбики; по часам (сотни точек) — сложенные области по счётчикам, чтобы не было «шума» из тонких полосок.
  const yv = (v) => Hh - v / max * (Hh - 10);
  let bars;
  if (S2.length > 120) {
    const acc = S2.map(() => 0);
    bars = ids.map((id, j) => {
      const lo = acc.slice(); S2.forEach((s, i) => { acc[i] += (s.by && s.by[id]) || 0; });
      const top = S2.map((s, i) => `${(i * bw).toFixed(1)},${yv(acc[i]).toFixed(1)}`), bot = S2.map((s, i) => `${(i * bw).toFixed(1)},${yv(lo[i]).toFixed(1)}`).reverse();
      return `<polygon points="${top.concat(bot).join(" ")}" fill="${cols[j % 4]}" fill-opacity=".85"><title>${esc(meters[j].name)}</title></polygon>`;
    }).join("");
  } else bars = S2.map((s, i) => { let y = Hh; return ids.map((id, j) => { const v = (s.by && s.by[id]) || 0; const hh = v / max * (Hh - 10); y -= hh; return `<rect x="${(i * bw).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(0.6, bw - (bw > 4 ? 1 : 0)).toFixed(1)}" height="${hh.toFixed(1)}" fill="${cols[j % 4]}"><title>${esc(s.t)} · ${esc(meters[j].name)}: ${nf(v, 0)} кВт·ч</title></rect>`; }).join(""); }).join("");
  const unit = EN.hg === "day" ? "МВт·ч/сутки" : "кВт·ч/час";
  const tick = S2.length ? [0, Math.floor(S2.length / 2), S2.length - 1].map((i, n) => `<text x="${(i * bw).toFixed(0)}" y="${Hh + 14}" class="tk" text-anchor="${["start", "middle", "end"][n]}">${esc(S2[i].t)}</text>`).join("") : "";
  $("#main").innerHTML = `
    <div class="kpis">
      <div class="k"><div class="lbl">Всего за период</div><div class="num" style="font-size:20px">${nf(h.total_kwh / 1000, 1)} МВт·ч</div></div>
      <div class="k"><div class="lbl">Средняя мощность</div><div class="num" style="font-size:20px">${nf((h.avg_kw || 0) / 1000, 2)} МВт</div></div>
      <div class="k"><div class="lbl">Пик</div><div class="num" style="font-size:20px">${h.peak ? nf(h.peak.kwh / (EN.hg === "day" ? 24000 : 1000), 2) + " МВт" : "—"}</div><div class="hint">${h.peak ? esc(h.peak.t) : ""}</div></div>
      <div class="k"><div class="lbl">Счётчиков</div><div class="num" style="font-size:20px">${nf(meters.length)}</div><div class="hint">${esc([...new Set(meters.map((m) => m.substation).filter(Boolean))].join(", ") || "")}</div></div>
    </div>
    <div class="w"><div class="wh">Почасовки энергокомпании<span class="sp"></span><span class="seg" id="enHg"><button data-g="hour" class="${EN.hg === "hour" ? "on" : ""}">По часам</button><button data-g="day" class="${EN.hg === "day" ? "on" : ""}">По суткам</button></span>
      <label class="btn sm" style="margin-left:8px;cursor:pointer">Загрузить файл<input type="file" id="enFile" accept=".xls,.xlsx" hidden></label></div>
    <div class="wb"><div class="legend" style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:6px">${meters.map((m, j) => `<span><i class="dot" style="background:${cols[j % 4]}"></i> ${esc(m.name)} <span class="faint num">${nf(m.kwh / 1000, 0)} МВт·ч</span></span>`).join("")}<span class="sp"></span><span class="faint">${unit}</span></div>
      <svg viewBox="0 0 ${W} ${Hh + 18}" class="enh" style="width:100%;height:auto;display:block">${bars}${tick}</svg>
      <div class="hint">Файл почасовок от энергокомпании (.xls/.xlsx). Повторная загрузка не задваивает часы, введённые вручную показания не перезаписываются.</div></div></div>`;
  $$("#enHg button").forEach((b) => b.onclick = () => { EN.hg = b.dataset.g; go("energy", true); });
  $("#enFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    const t = tok.get();
    const r = await fetch(API + "/energy/import", { method: "POST", body: fd, headers: t ? { Authorization: "Bearer " + t.access_token } : {} });
    const j = await r.json().catch(() => ({}));
    toast(r.ok ? `Загружено: ${nf(j.rows || j.inserted || 0)} часов` : (j.detail || "Не удалось загрузить"));
    if (r.ok) go("energy", true);
  };
}
async function enPageMeters() {
  const q = EN.seq;
  const meters = await api("/energy/meters");
  if (enStale(q)) return;
  const reg = [...EN_FEED.map((f) => ({ serial: f.meter, name: `${f.n} (${f.full})`, where: f.site, kind: "фидер" })),
    ...Object.entries(EN_KTPM).map(([k, s]) => ({ serial: s, name: `КТП ${k}`, where: EN_FEED[k <= 5 ? 0 : 1].n, kind: "КТП" }))];
  const have = new Set(meters.map((m) => m.serial));
  $("#main").innerHTML = `
    <div class="w"><div class="wh">Счётчики с почасовками</div><div class="wb scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Название</th><th>Подстанция</th><th>Тип</th><th class="num">Часов</th><th class="num">Всего, МВт·ч</th><th>Период</th></tr></thead><tbody>
      ${meters.map((m) => `<tr><td class="num">${esc(m.serial)}</td><td>${esc(m.name)}</td><td>${esc(m.substation || "—")}</td><td>${m.kind === "commercial" ? "коммерческий" : "технический"}</td><td class="num">${nf(m.hours)}</td><td class="num">${nf(m.kwh / 1000, 1)}</td><td>${m.start ? tfmt(m.start) + " – " + tfmt(m.end) : "—"}</td></tr>`).join("") || '<tr><td colspan="7" class="hint">Пока нет</td></tr>'}
    </tbody></table></div></div>
    <div class="w" style="margin-top:12px"><div class="wh">Реестр счётчиков Диагонали</div><div class="wb scroll"><table class="tbl"><thead><tr><th>Номер</th><th>Узел</th><th>Фидер / площадка</th><th>Почасовки</th></tr></thead><tbody>
      ${reg.map((r) => `<tr><td class="num">${esc(r.serial)}</td><td>${esc(r.name)}</td><td>${esc(r.where)}</td><td>${have.has(r.serial) ? "есть" : '<span class="faint">ждём файл</span>'}</td></tr>`).join("")}
    </tbody></table><div class="hint" style="margin-top:8px">Почасовки ГПП-16 (Ф-6, Ф-75, Ф-76) относятся к другому объекту — привяжем к площадке после подтверждения.</div></div></div>`;
}

// ---------- global search ----------
(function () {
  const inp = $("#gq"), box = $("#gqRes"); let items = [], hi = 0;
  const norm = (s) => String(s || "").toLowerCase();
  const run = async () => {
    const q = norm(inp.value.trim()); if (q.length < 2) { box.hidden = true; return; }
    const devs = await loadDevices(); await loadLayouts();
    const qx = q.replace(/[.\s]/g, "x");
    const score = (d) => { const w = norm(d.worker); if (norm(d.ip) === q) return 0; if (w.endsWith(qx) || w.endsWith(q)) return 1; if (norm(d.serial) === q || norm(d.num) === q) return 1; if (w.includes(qx)) return 2; if ([d.ip, d.serial, d.mac, d.num].some((x) => norm(x).includes(q))) return 3; if (norm(d.client).includes(q) || norm(d.model).includes(q)) return 5; return 9; };
    items = devs.map((d) => [score(d), d]).filter((x) => x[0] < 9).sort((a, b) => a[0] - b[0]).slice(0, 8).map((x) => x[1]);
    const cs = S.sum ? S.sum.containers.filter((c) => c.id && norm(c.name).includes(q)).slice(0, 3) : [];
    hi = 0;
    box.innerHTML = cs.map((c) => `<button class="gr" data-gc="${c.id}"><span class="kind k-air">Контейнер</span> <b>${esc(c.name)}</b></button>`).join("") + items.map((d, i) => `<button class="gr ${i === 0 && !cs.length ? "hi" : ""}" data-gd="${d.id}">${stPill(d.status)} <b>${esc(d.model || "")}</b> <span class="num">${esc(d.worker || d.ip || "")}</span><br><span class="faint">${esc((d.container || "").split(" ").slice(0, 2).join(" "))} · ${esc(placeTxt(d, true))} · ${esc(d.ip || "без IP")}${d.client ? " · " + esc(d.client) : ""}</span></button>`).join("") || '<div class="gr faint">Ничего не нашлось</div>';
    box.hidden = false;
  };
  let t; inp.oninput = () => { clearTimeout(t); t = setTimeout(run, 180); };
  inp.onfocus = () => inp.value.trim().length >= 2 && run();
  inp.onkeydown = (e) => { if (e.key === "Enter") { const b = $(".gr.hi", box) || $(".gr[data-gd],.gr[data-gc]", box); if (b) b.click(); } if (e.key === "Escape") { box.hidden = true; inp.blur(); } };
  box.onclick = (e) => { const b = e.target.closest(".gr"); if (!b) return; box.hidden = true; if (b.dataset.gc) go("cont", false, b.dataset.gc); if (b.dataset.gd) openCard(+b.dataset.gd); };
  document.addEventListener("click", (e) => { if (!e.target.closest(".gsearch")) box.hidden = true; });
})();

$$(".tab[data-page]").forEach((n) => n.onclick = () => { if (n.dataset.page === "asics") resetF(); go(n.dataset.page); });
// площадки и клиенты: см. msBox
$("#themeBtn").onclick = () => { const h = document.documentElement; h.dataset.theme = h.dataset.theme === "dark" ? "light" : "dark"; localStorage.setItem("kirka_theme", h.dataset.theme); };
$("#logoutBtn").onclick = () => { tok.clear(); showLogin(); };
document.documentElement.dataset.theme = localStorage.getItem("kirka_theme") || "dark";
window.addEventListener("hashchange", () => {
  const st = history.state || {};
  try { if (st.f) Object.assign(S.f, JSON.parse(st.f)); if (st.ef) Object.assign(S.ef, JSON.parse(st.ef)); } catch (e) { /* ignore */ }
  const [p, a] = location.hash.slice(1).split("/"); if (p !== S.page || (a || null) !== S.arg) go(p, false, a);
});
window.addEventListener("popstate", () => { const [p, a] = location.hash.slice(1).split("/"); const st = history.state || {}; try { if (st.f) Object.assign(S.f, JSON.parse(st.f)); if (st.ef) Object.assign(S.ef, JSON.parse(st.ef)); } catch (e) { /* ignore */ } if (p !== S.page || (a || null) !== S.arg) go(p, false, a); });
$("#backBtn").onclick = () => history.back();

let tick = null;
async function boot() {
  // one-time login link from the bot: /#s=<session_id>
  const m = location.hash.match(/[#&]s=([\w-]+)/);
  if (m) {
    try { const st = await (await fetch(API + "/auth/qr/status/" + encodeURIComponent(m[1]))).json(); if (st.status === "approved" && st.access_token) tok.set(st); } catch (e) { /* ignore */ }
    const pg = (location.hash.match(/[#&]p=(\w+)/) || [])[1] || "today";
    history.replaceState(null, "", "#" + pg);
  }
  if (!tok.get()) return showLogin();
  $("#login").hidden = true; $("#app").hidden = false;
  try { const me = await api("/auth/me"); $("#av").textContent = ((me.full_name || me.email || "K")[0] || "K").toUpperCase(); } catch (e) { if (e.message === "auth") return showLogin(); }
  const [p, a] = (location.hash || "#today").slice(1).split("/");
  await go(PAGES[p] ? p : "today", false, a);
  clearInterval(tick);
  tick = setInterval(() => { if (S.page === "calc" || S.page === "notify" || (S.page === "energy" && EN.sub !== "scheme") || document.hidden || !$("#drawer").hidden || !$("#modal").hidden || S.sel.size || document.activeElement.matches("input,select") || $(".msel.open") || $(".perpop")) return; S.devs = null; S.sumAt = 0; go(S.page, true, S.arg); }, 60000);
}
boot();
