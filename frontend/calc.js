"use strict";
// Kirkalab admin UI — vanilla JS client for the FastAPI backend.
const API = "/api/v1";
const TOKENS = "kirkalab_tokens";

function loadTokens() { try { return JSON.parse(localStorage.getItem(TOKENS)) || null; } catch (e) { return null; } }
function saveTokens(t) { localStorage.setItem(TOKENS, JSON.stringify(t)); }
function clearTokens() { localStorage.removeItem(TOKENS); }

function $(id) { return document.getElementById(id); }
function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

async function api(path, options, retry) {
  const opts = options || {};
  opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const tokens = loadTokens();
  if (tokens && tokens.access_token) { opts.headers["Authorization"] = "Bearer " + tokens.access_token; }
  const resp = await fetch(API + path, opts);
  if (resp.status === 401 && tokens && tokens.refresh_token && !retry) {
    const ok = await tryRefresh();
    if (ok) { return api(path, options, true); }
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (!resp.ok) { const msg = (data && (data.detail || data.message)) || ("Ошибка " + resp.status); throw new Error(msg); }
  return data;
}

async function tryRefresh() {
  const tokens = loadTokens();
  if (!tokens || !tokens.refresh_token) { return false; }
  try {
    const resp = await fetch(API + "/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: tokens.refresh_token }) });
    if (!resp.ok) { return false; }
    const data = await resp.json();
    saveTokens(data);
    return true;
  } catch (e) { return false; }
}

async function doLogin(email, password) {
  const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ email: email, password: password }) });
  saveTokens(data);
}

async function loadProfile() {
  const me = await api("/auth/me");
  const dl = $("profile");
  dl.innerHTML = "";
  const rows = [["ID", me.id], ["Email", me.email], ["Handle", me.handle], ["Активен", me.is_active ? "да" : "нет"], ["Админ", me.is_admin ? "да" : "нет"], ["Создан", me.created_at]];
  for (const [k, v] of rows) { const dt = document.createElement("dt"); dt.textContent = k; const dd = document.createElement("dd"); dd.textContent = String(v); dl.appendChild(dt); dl.appendChild(dd); }
  $("whoami").textContent = me.email;
  return me;
}

async function loadUsers() {
  const body = $("users-body");
  $("users-msg").textContent = "";
  try {
    const users = await api("/users/");
    body.innerHTML = "";
    for (const u of users) {
      const tr = document.createElement("tr");
      const cells = [u.id, u.email, u.handle, u.is_active ? "✓" : "—", u.is_admin ? "✓" : "—", u.created_at];
      for (const c of cells) { const td = document.createElement("td"); td.textContent = String(c); tr.appendChild(td); }
      body.appendChild(tr);
    }
  } catch (e) { $("users-msg").textContent = e.message; }
}

async function enterDashboard() {
  hide("login-view"); hide("reset-view"); show("dashboard-view"); show("logout-btn");
  const me = await loadProfile();
  if (me.is_admin) { show("users-card"); await loadUsers(); } else { hide("users-card"); }
}

function enterLogin() {
  clearTokens();
  show("login-view"); hide("reset-view"); hide("dashboard-view"); hide("logout-btn");
  $("whoami").textContent = "";
}

document.addEventListener("DOMContentLoaded", function () {
  $("login-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    $("login-error").textContent = "";
    try { await doLogin($("login-email").value, $("login-password").value); await enterDashboard(); }
    catch (e) { $("login-error").textContent = e.message; }
  });

  $("logout-btn").addEventListener("click", enterLogin);
  $("show-reset").addEventListener("click", function (ev) { ev.preventDefault(); hide("login-view"); show("reset-view"); });
  $("show-login").addEventListener("click", function (ev) { ev.preventDefault(); hide("reset-view"); show("login-view"); });

  $("reset-request-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    try { const r = await api("/auth/password-reset/request", { method: "POST", body: JSON.stringify({ email: $("reset-email").value }) }); $("reset-msg").textContent = r.detail + (r.reset_token ? (" Токен: " + r.reset_token) : ""); if (r.reset_token) { $("reset-token").value = r.reset_token; } }
    catch (e) { $("reset-msg").textContent = e.message; }
  });

  $("reset-confirm-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    try { const r = await api("/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token: $("reset-token").value, new_password: $("reset-new-password").value }) }); $("reset-msg").textContent = r.detail; }
    catch (e) { $("reset-msg").textContent = e.message; }
  });

  $("verify-email-btn").addEventListener("click", async function () {
    $("profile-msg").textContent = "";
    try { const req = await api("/auth/verify-email/request", { method: "POST" }); const r = await api("/auth/verify-email", { method: "POST", body: JSON.stringify({ token: req.email_verify_token }) }); $("profile-msg").textContent = r.detail + ": " + r.email; }
    catch (e) { $("profile-msg").textContent = e.message; }
  });

  $("refresh-users").addEventListener("click", loadUsers);

  const tokens = loadTokens();
  if (tokens && tokens.access_token) { enterDashboard().catch(function () { enterLogin(); }); } else { enterLogin(); }
});
