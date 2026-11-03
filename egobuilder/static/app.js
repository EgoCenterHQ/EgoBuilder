"use strict";
// EgoBuilder UI: the profile lives in P, every input is bound to a dotted path
// (data-bind="system.hostname"); the server validates, previews and builds.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const DRAFT = "egobuilder.draft.v1";
let META, P, V = { errors: [], warnings: [] }, step = 0;
let previewFiles = [], fileSel = null, fileView = "diff", job = null, logOffset = 0, pollTimer = null;

// ---------------------------------------------------------------- utils
function get(path, o = P) { return path.split(".").reduce((a, k) => (a == null ? a : a[k]), o); }
function set(path, v, o = P) {
  const ks = path.split("."); const last = ks.pop();
  const t = ks.reduce((a, k) => (a[k] ??= {}), o); t[last] = v;
}
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function randomPw(n = 16) {
  const abc = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const b = crypto.getRandomValues(new Uint32Array(n));
  return [...b].map(x => abc[x % abc.length]).join("");
}
function deepMerge(base, over) {
  if (Array.isArray(base) || typeof base !== "object" || base === null) return over === undefined ? base : over;
  const out = { ...base };
  for (const [k, v] of Object.entries(over || {}))
    out[k] = (k in base && typeof base[k] === "object" && !Array.isArray(base[k]) && v && typeof v === "object")
      ? deepMerge(base[k], v) : v;
  return out;
}
const fresh = () => structuredClone(META.default_profile);
const appMeta = id => META.apps.find(a => a.id === id);

// ---------------------------------------------------------------- draft
function saveDraft() {
  const d = structuredClone(P);
  d.accounts.root_password = ""; d.accounts.password = "";      // passwords never in the browser storage
  try { localStorage.setItem(DRAFT, JSON.stringify(d)); } catch (e) { /* private mode */ }
}
function loadDraft() {
  try { const d = JSON.parse(localStorage.getItem(DRAFT) || "null"); return d && deepMerge(fresh(), d); }
  catch (e) { return null; }
}

// ---------------------------------------------------------------- binding
function readInput(inp) {
  if (inp.type === "checkbox") return inp.checked;
  if (inp.type === "number") return inp.value === "" ? "" : Number(inp.value);
  if (inp.hasAttribute("data-list")) return inp.value.split(/\s+/).filter(Boolean);
  return inp.value;
}
function writeInput(inp) {
  const v = get(inp.dataset.bind);
  if (inp.type === "checkbox") inp.checked = !!v;
  else if (inp.type === "radio") inp.checked = inp.value === v;
  else if (inp.hasAttribute("data-list")) { if (document.activeElement !== inp) inp.value = (v || []).join(" "); }
  else if (document.activeElement !== inp || inp.tagName === "SELECT") inp.value = v ?? "";
}
function syncInputs(root = document) { $$("[data-bind]", root).forEach(writeInput); }

document.addEventListener("input", onBound);
document.addEventListener("change", onBound);
function onBound(ev) {
  const inp = ev.target;
  if (!inp.dataset || !inp.dataset.bind) return;
  if (inp.type === "radio" && !inp.checked) return;
  if (ev.type === "input" && (inp.type === "checkbox" || inp.type === "radio" || inp.tagName === "SELECT")) return;
  const path = inp.dataset.bind;
  set(path, readInput(inp));
  if (path === "system.locale") {                       // the language drives the regional defaults
    const l = META.locales[P.system.locale];
    if (l) { P.system.keyboard = l.keyboard; P.system.timezone = l.timezone; P.setup.wifi_country = l.country; }
    syncInputs();
  }
  if (/^apps\.[^.]+\.enabled$/.test(path)) renderApps();
  changed();
}
function changed() {
  saveDraft(); refreshConditionals(); refreshDerived(); validateSoon();
  if (step === 4) previewSoon();
}
function refreshConditionals() {
  $$("[data-if]").forEach(e => {
    const [path, val] = e.dataset.if.split("=");
    const v = get(path);
    e.hidden = val === undefined ? !v : String(v) !== val;
  });
}
function refreshDerived() {
  $$("[data-show=host]").forEach(e => e.textContent = `${P.system.hostname}.local`);
  $$("[data-show=setup]").forEach(e => e.textContent = `${P.setup.mdns_name}.local`);
  const n = Object.values(P.apps).filter(a => a.enabled).length + P.custom_apps.filter(c => c.enabled).length;
  $("#apps-count").textContent = `${n} apps selected`;
}

// ---------------------------------------------------------------- static selects
function fillSelects() {
  $("#locale").replaceChildren(...Object.entries(META.locales).map(([k, l]) => el("option", { value: k, text: `${l.label} (${k})` })));
  $("#keyboard").replaceChildren(...Object.entries(META.keyboards).map(([k, l]) => el("option", { value: k, text: `${l} (${k})` })));
  $("#tzlist").replaceChildren(...META.timezones.map(t => el("option", { value: t })));
  $("#partitioning").replaceChildren(...Object.entries(META.partitioning).map(([k, l]) =>
    el("label", {}, el("input", { type: "radio", name: "part", "data-bind": "install.partitioning", value: k }), ` ${l}`)));
  $("#egos-version").textContent = `EgOS ${META.egos_version}`;
}

// ---------------------------------------------------------------- apps
function renderApps() {
  const q = ($("#apps-filter").value || "").toLowerCase();
  const box = $("#apps"); const open = new Set($$("details[open]", box).map(d => d.dataset.app));
  box.replaceChildren();
  const cats = [...new Set(META.apps.map(a => a.category))];
  for (const cat of cats) {
    const apps = META.apps.filter(a => a.category === cat &&
      (!q || (a.name + a.description).toLowerCase().includes(q)));
    if (!apps.length) continue;
    box.append(el("div", { class: "cat", text: cat }), el("div", { class: "app-grid" }, apps.map(a => appCard(a, open.has(a.id)))));
  }
  syncInputs(box); markErrors();
}
function appCard(a, open) {
  const cfg = P.apps[a.id]; const base = `apps.${a.id}`;
  const tags = [];
  if (a.recommended) tags.push(el("span", { class: "tag rec", text: "recommended" }));
  for (const [k, p] of Object.entries(a.ports)) tags.push(el("span", { class: "tag mono", text: `${cfg.ports[k] ?? p.default}/${p.proto}` }));
  if (a.autosetup) tags.push(el("span", { class: "tag auto", text: "automatic setup" }));
  if (a.build_var) tags.push(el("span", { class: "tag", text: "EgOS stock" }));
  const det = el("details", { "data-app": a.id, open },
    el("summary", { text: "Settings" }),
    el("div", { class: "grid" },
      el("label", { class: "field" }, el("span", { text: "Version (tag)" }),
        el("input", { "data-bind": `${base}.version`, spellcheck: "false" })),
      Object.entries(a.ports).map(([k, p]) => el("label", { class: "field" }, el("span", { text: `Port ${p.label}` }),
        el("input", { type: "number", min: 1, max: 65535, "data-bind": `${base}.ports.${k}`, disabled: p.fixed }))),
      Object.entries(a.secrets).map(([k, label]) => el("label", { class: "field" }, el("span", { text: label }),
        el("input", { "data-bind": `${base}.secrets.${k}`, placeholder: "random (generated on the machine)", spellcheck: "false" }))),
      Object.entries(a.options).map(([k, o]) => el("label", { class: "field" }, el("span", { text: o.label }),
        o.choices ? el("select", { "data-bind": `${base}.options.${k}` }, o.choices.map(c => el("option", { value: c, text: c })))
                  : el("input", { "data-bind": `${base}.options.${k}` })))),
    a.autosetup && el("p", { class: "hint", text: "Automatic: " + a.autosetup }),
    a.firstrun && el("p", { class: "hint", text: "First visit: " + a.firstrun }),
    a.notes && el("p", { class: "hint", text: "Note: " + a.notes }),
    (a.pool_dirs.length || a.home_dirs.length) && el("p", { class: "hint mono",
      text: [...a.pool_dirs.map(d => `pool/${d}`), ...a.home_dirs.map(d => `~/${d}`)].join("  ") }),
    el("details", {}, el("summary", { text: "Compose and .env" }), el("pre", { text: a.compose + "\n" + a.env })));
  return el("div", { class: `card app${cfg.enabled ? " on" : ""}`, "data-app-card": a.id },
    el("label", { class: "app-head" },
      el("input", { type: "checkbox", "data-bind": `${base}.enabled` }),
      el("div", {}, el("b", { text: a.name }), el("p", { text: a.description }), el("div", { class: "tags" }, tags))),
    det);
}

function renderCustoms() {
  const box = $("#customs"); box.replaceChildren();
  P.custom_apps.forEach((c, i) => {
    const node = $("#tpl-custom").content.firstElementChild.cloneNode(true);
    node.dataset.customIdx = i;
    $$("[data-c]", node).forEach(inp => {
      const k = inp.dataset.c;
      if (inp.type === "checkbox") inp.checked = c[k] !== false;
      else inp.value = Array.isArray(c[k]) ? c[k].join(" ") : (c[k] || "");
      const upd = () => {
        c[k] = inp.type === "checkbox" ? inp.checked : (k.endsWith("_dirs") ? inp.value.split(/[\s,]+/).filter(Boolean) : inp.value);
        changed();
      };
      inp.addEventListener("input", upd); inp.addEventListener("change", upd);
    });
    $("[data-c-remove]", node).addEventListener("click", () => { P.custom_apps.splice(i, 1); renderCustoms(); changed(); });
    box.append(node);
  });
}

// ---------------------------------------------------------------- validation
const STEP_OF = f => /^(arch|profile_name|system)/.test(f) ? 0 : /^(accounts|setup)/.test(f) ? 1
  : /^(apps|custom)/.test(f) ? 2 : /^(services|packages|install)/.test(f) ? 3 : 4;
const validateSoon = debounce(async () => {
  try { V = await api("/api/validate", { profile: P }); } catch (e) { V = { errors: [{ field: "", msg: e.message }], warnings: [] }; }
  markErrors(); if (step === 4) renderSummary();
}, 250);

function markErrors() {
  $$(".invalid").forEach(e => e.classList.remove("invalid"));
  $$(".err-msg").forEach(e => e.remove());
  const counts = [0, 0, 0, 0, 0];
  for (const e of V.errors) {
    counts[STEP_OF(e.field)]++;
    let target = $(`[data-bind="${CSS.escape(e.field)}"]`);
    if (e.field.startsWith("apps.")) {
      const card = $(`[data-app-card="${CSS.escape(e.field.split(".")[1])}"]`);
      card && card.classList.add("invalid");
      if (target && card) { const d = target.closest("details"); d && (d.open = true); }
      if (!target && card) target = $(".app-head", card);
    }
    if (e.field.startsWith("custom.")) {
      const id = e.field.split(".")[1];
      const i = P.custom_apps.findIndex(c => c.id === id || (!c.id && id === "?"));
      const card = $(`[data-custom-idx="${i}"]`);
      if (card) { card.classList.add("invalid"); target = $("[data-c=compose]", card); }
    }
    if (!target) continue;
    const field = target.closest(".field, .check, .app-head") || target;
    field.classList.add("invalid");
    if (!$(".err-msg", field)) field.append(el("small", { class: "err-msg", text: e.msg }));
  }
  $$("#steps button").forEach((b, i) => {
    const badge = $(".badge", b); badge.hidden = !counts[i]; badge.textContent = counts[i];
  });
  const st = $("#vstatus");
  st.textContent = V.errors.length ? `${V.errors.length} to fix` : (V.warnings.length ? `ready · ${V.warnings.length} warnings` : "ready");
  st.className = "status" + (V.errors.length ? " bad" : "");
}

// ---------------------------------------------------------------- steps
function go(n) {
  step = Math.max(0, Math.min(4, n));
  $$("section.step").forEach(s => s.hidden = Number(s.dataset.step) !== step);
  $$("#steps button").forEach(b => b.classList.toggle("on", Number(b.dataset.step) === step));
  $("#prev").style.visibility = step ? "visible" : "hidden";
  $("#next").style.visibility = step < 4 ? "visible" : "hidden";
  if (step === 4) { renderSummary(); previewSoon(); loadJobs(); }
  window.scrollTo({ top: 0 });
  try { localStorage.setItem("egobuilder.step", step); } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- summary
function appUrl(a, cfg) {
  if (!a.ports.web) return null;
  const scheme = a.id === "portainer" ? "https" : "http";
  return `${scheme}://${P.system.hostname}.local:${cfg.ports.web ?? a.ports.web.default}`;
}
function renderSummary() {
  const l = META.locales[P.system.locale] || {};
  const sel = META.apps.filter(a => P.apps[a.id].enabled);
  const customs = P.custom_apps.filter(c => c.enabled);
  const items = [
    ["Language", `${l.label || P.system.locale} · keyboard ${P.system.keyboard}`],
    ["Time zone", P.system.timezone],
    ["Hostname", `${P.system.hostname}.${P.system.domain}`],
    ["User", P.accounts.user_mode === "preset" ? `${P.accounts.user} (preset)` : "chosen at first boot"],
    ["Root", !P.accounts.root_password ? "password not set" : P.accounts.lock_root ? "locked after installation" : "password set"],
    ["Apps", [...sel.map(a => a.name), ...customs.map(c => c.name || c.id)].join(", ") || "none"],
    ["Services", [P.services.ssh && "SSH", P.services.cockpit && "Cockpit", P.services.samba && "Samba",
      P.services.auto_updates && "automatic updates"].filter(Boolean).join(", ") || "none"],
  ];
  $("#summary").replaceChildren(...items.map(([k, v]) => el("div", { class: "card" }, el("b", { text: k }), el("span", { text: v }))));

  const rows = sel.map(a => [a.name, appUrl(a, P.apps[a.id])]).filter(r => r[1]);
  if (P.services.cockpit) rows.push(["Cockpit", `https://${P.system.hostname}.local:9090`]);
  if (P.services.samba) rows.push(["Shared folder", `\\\\${P.system.hostname}\\media  ·  smb://${P.system.hostname}.local/media`]);
  if (P.accounts.user_mode === "firstboot") rows.unshift(["Setup (1st boot)", `Wi-Fi “${P.setup.ssid}” or http://${P.setup.mdns_name}.local`]);
  $("#urls").replaceChildren(...rows.map(([k, v]) => el("tr", {}, el("td", { text: k }), el("td", { class: "mono", text: v }))));

  const box = $("#problems"); box.replaceChildren();
  if (V.errors.length) box.append(el("div", { class: "note err" }, el("b", { text: "To fix before building" }),
    el("ul", {}, V.errors.map(e => el("li", {}, el("a", { href: "#", onclick: ev => { ev.preventDefault(); go(STEP_OF(e.field)); } }, e.msg))))));
  if (V.warnings.length) box.append(el("div", { class: "note warn" }, el("b", { text: "Warnings" }),
    el("ul", {}, V.warnings.map(w => el("li", { text: w.msg })))));
  $("#stage").disabled = V.errors.length > 0;
  const e = META.engine;
  $("#engine").textContent = e.engine === "local" ? (e.can_build ? "Build inside the EgoBuilder container: ~6-10 min, downloads ~1 GB."
      : "The container is not privileged: restart it with --privileged (see below) to build the ISO.")
    : e.can_build ? `Build from the browser with ${e.engine}${e.root ? " (root)" : " (sudo)"}: ~6-10 min, downloads ~1 GB.`
    : `Build from the browser not available (${!e.engine ? "docker/podman missing" : "needs root or passwordless sudo"}): you will get the command.`;
}

// ---------------------------------------------------------------- preview
const previewSoon = debounce(async () => {
  try { previewFiles = (await api("/api/preview", { profile: P })).files; }
  catch (e) { previewFiles = []; $("#file-content").textContent = "Preview not available: " + e.message; }
  const order = { changed: 0, new: 1, deleted: 2, same: 3 };
  previewFiles.sort((a, b) => order[a.status] - order[b.status] || a.path.localeCompare(b.path));
  if (!previewFiles.find(f => f.path === fileSel)) fileSel = (previewFiles.find(f => f.status !== "same") || previewFiles[0] || {}).path;
  renderFiles();
}, 300);
const STATUS_LABEL = { changed: "changed", new: "new", deleted: "removed", same: "unchanged" };
function renderFiles() {
  $("#file-list").replaceChildren(...previewFiles.map(f => el("li", { class: f.path === fileSel ? "on" : "",
    onclick: () => { fileSel = f.path; renderFiles(); } }, el("span", { text: f.path }), el("em", { class: f.status, text: STATUS_LABEL[f.status] }))));
  const f = previewFiles.find(x => x.path === fileSel);
  const pre = $("#file-content"); pre.replaceChildren();
  if (!f) return;
  if (fileView === "content") { pre.textContent = f.content ?? "(file removed from the image)"; return; }
  if (!f.diff) { pre.textContent = "No difference from the repo."; return; }
  for (const line of f.diff.split("\n")) {
    const cls = line.startsWith("@@") ? "d-hunk" : line.startsWith("+") ? "d-add" : line.startsWith("-") ? "d-del" : null;
    pre.append(cls ? el("span", { class: cls, text: line }) : line, "\n");
  }
}
$$(".file-tabs button").forEach(b => b.addEventListener("click", () => {
  fileView = b.dataset.view; $$(".file-tabs button").forEach(x => x.classList.toggle("on", x === b)); renderFiles();
}));

// ---------------------------------------------------------------- jobs
async function stage() {
  const btn = $("#stage"); btn.disabled = true; btn.textContent = "Preparing…";
  try { job = await api("/api/stage", { profile: P }); renderJob(); loadJobs(); }
  catch (e) { $("#job").replaceChildren(el("div", { class: "note err", text: e.message })); }
  finally { btn.disabled = V.errors.length > 0; btn.textContent = "Prepare the tree"; }
}
function copyBox(text) {
  return el("div", { class: "cmd" }, el("pre", { text }),
    el("button", { class: "btn small", type: "button", onclick: ev => { navigator.clipboard.writeText(text); ev.target.textContent = "Copied"; } }, "Copy"));
}
const STATE_LABEL = { staged: "tree ready", "check-failed": "check failed", building: "building", done: "ISO ready",
  failed: "build failed", cancelled: "cancelled", interrupted: "interrupted", staging: "preparing" };
function renderJob() {
  const box = $("#job"); box.replaceChildren();
  if (!job) return;
  const base = `/api/jobs/${job.id}`;
  const e = META.engine;
  const canBuild = e.can_build && ["staged", "failed", "cancelled", "interrupted"].includes(job.status) && !job.isos.length;
  box.append(el("div", { class: "job card" },
    el("div", { class: "row" }, el("b", { text: `Job ${job.id}` }), el("span", { class: `pill ${job.status}`, text: STATE_LABEL[job.status] || job.status })),
    job.check && el("details", { open: !job.check.ok }, el("summary", { text: job.check.ok ? "tools/check.sh: ok" : "tools/check.sh: FAILED" }), el("pre", { text: job.check.output })),
    el("div", { class: "row" },
      el("a", { class: "btn", href: `${base}/tree.tar.gz` }, "Download tree (.tar.gz)"),
      canBuild && el("button", { class: "btn primary", type: "button", onclick: build }, "Start ISO build"),
      job.status === "building" && el("button", { class: "btn danger", type: "button", onclick: cancel }, "Cancel build"),
      job.isos.map(i => el("a", { class: "btn primary", href: `${base}/iso/${encodeURIComponent(i)}` }, `Download ${i}`))),
    e.engine === "local" && !e.can_build && el("div", {},
      el("p", { class: "hint", text: "The container runs without --privileged: live-build cannot mount /proc. Restart it like this (jobs stay in the volume):" }),
      copyBox("docker compose up -d   # compose.yaml already has privileged: true\n# or\ndocker run -d --name egobuilder --privileged -p 127.0.0.1:8765:8765 -v egobuilder-data:/data egobuilder")),
    !e.can_build && e.engine !== "local" && el("div", {},
      el("p", { class: "hint", text: "Run on your computer (needs sudo); the ISO lands in the job's iso/ folder:" }),
      copyBox(job.command)),
    ["building", "done", "failed", "cancelled", "interrupted"].includes(job.status) && el("pre", { class: "log", id: "log" })));
  if (["building", "done", "failed", "cancelled", "interrupted"].includes(job.status)) { logOffset = 0; pollLog(); }
}
async function pollLog() {
  clearTimeout(pollTimer);
  if (!job) return;
  try {
    const r = await api(`/api/jobs/${job.id}/log?offset=${logOffset}`);
    const pre = $("#log");
    if (pre && r.text) { const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30; pre.append(r.text); if (atEnd) pre.scrollTop = pre.scrollHeight; }
    logOffset = r.offset;
    if (r.status !== job.status) { job = await api(`/api/jobs/${job.id}`); renderJob(); loadJobs(); return; }
    if (r.status === "building") pollTimer = setTimeout(pollLog, 1500);
  } catch (e) { pollTimer = setTimeout(pollLog, 4000); }
}
async function build() {
  try { job = await api(`/api/jobs/${job.id}/build`, {}); renderJob(); loadJobs(); }
  catch (e) { alert(e.message); }
}
async function cancel() {
  if (!confirm("Cancel the running build?")) return;
  try { await api(`/api/jobs/${job.id}/cancel`, {}); } catch (e) { alert(e.message); }
}
async function loadJobs() {
  let jobs = [];
  try { jobs = (await api("/api/jobs")).jobs; } catch (e) { return; }
  const box = $("#jobs");
  if (!jobs.length) { box.replaceChildren(el("p", { class: "hint", text: "No builds yet." })); return; }
  box.replaceChildren(...jobs.map(j => el("div", { class: "jrow" },
    el("span", { class: `pill ${j.status}`, text: STATE_LABEL[j.status] || j.status }),
    el("b", { text: j.profile_name }), el("span", { class: "mono", text: `${j.created} · ${(j.apps || []).join(", ")}` }),
    el("a", { href: "#", onclick: ev => { ev.preventDefault(); job = j; renderJob(); $("#job").scrollIntoView({ behavior: "smooth" }); } }, "open"),
    j.isos.map(i => el("a", { href: `/api/jobs/${j.id}/iso/${encodeURIComponent(i)}` }, i)),
    j.status !== "building" && el("a", { href: "#", class: "danger", onclick: async ev => {
      ev.preventDefault(); if (!confirm(`Delete job ${j.id} and its files?`)) return;
      try { await api(`/api/jobs/${j.id}/delete`, {}); if (job && job.id === j.id) { job = null; renderJob(); } loadJobs(); } catch (e) { alert(e.message); }
    } }, "delete"))));
}

// ---------------------------------------------------------------- import / export
function exportProfile() {
  const withPw = confirm("Include the passwords in the file?\n\nOK = yes (keep it safe) · Cancel = no");
  const d = structuredClone(P);
  if (!withPw) { d.accounts.root_password = ""; d.accounts.password = ""; }
  const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" })),
    download: `egobuilder-${P.profile_name || "profile"}.json` });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$("#import").addEventListener("change", async ev => {
  const f = ev.target.files[0]; if (!f) return;
  try { P = deepMerge(fresh(), JSON.parse(await f.text())); P.custom_apps ||= []; rerender(); }
  catch (e) { alert("Invalid profile: " + e.message); }
  ev.target.value = "";
});
$("#export").addEventListener("click", exportProfile);
$("#reset").addEventListener("click", () => { if (confirm("Go back to the base EgOS profile?")) { P = fresh(); rerender(); } });

// ---------------------------------------------------------------- misc wiring
document.addEventListener("click", ev => {
  const gen = ev.target.closest("[data-gen]");
  if (gen) { set(gen.dataset.gen, randomPw(gen.dataset.gen.startsWith("setup") ? 12 : 16)); syncInputs(); changed();
    const inp = $(`[data-bind="${gen.dataset.gen}"]`); if (inp && inp.type === "password") inp.type = "text"; }
  const tg = ev.target.closest("[data-toggle-pw]");
  if (tg) { const inp = $("input", tg.parentElement); inp.type = inp.type === "password" ? "text" : "password"; tg.textContent = inp.type === "password" ? "Show" : "Hide"; }
});
$$("#steps button").forEach(b => b.addEventListener("click", () => go(Number(b.dataset.step))));
$("#prev").addEventListener("click", () => go(step - 1));
$("#next").addEventListener("click", () => go(step + 1));
$("#apps-filter").addEventListener("input", renderApps);
$("#add-custom").addEventListener("click", () => {
  P.custom_apps.push({ id: "", name: "", enabled: true, compose: "", env: "", pool_dirs: [], home_dirs: [] });
  renderCustoms(); changed();
});
$("#stage").addEventListener("click", stage);

function rerender() { syncInputs(); renderApps(); renderCustoms(); changed(); if (step === 4) renderSummary(); }

(async function init() {
  try { META = await api("/api/meta"); }
  catch (e) { document.body.replaceChildren(el("p", { class: "note err", text: "EgoBuilder server not reachable: " + e.message })); return; }
  P = loadDraft() || fresh();
  P.custom_apps ||= [];
  fillSelects(); rerender();
  let s = 0; try { s = Number(localStorage.getItem("egobuilder.step") || 0); } catch (e) { /* ignore */ }
  const h = location.hash.match(/step=(\d)/);
  go(h ? Number(h[1]) - 1 : s);
})();
