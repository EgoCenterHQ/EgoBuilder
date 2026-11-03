/* EgOS - Cockpit page "Storage pool": a front end for /usr/local/sbin/egos-storage.
 * Reads state with `egos-storage status --json` / `disks --json` and starts every
 * change as `egos-storage job ...` (a systemd unit), so closing the page never
 * interrupts a format or a copy. All safety checks stay in egos-storage. */
"use strict";

const TOOL = "/usr/local/sbin/egos-storage";
const POLL_IDLE = 10000;
const POLL_JOB = 2000;

let state = null;
let timer = null;
let lastJobRunning = false;

const $ = id => document.getElementById(id);

function run(args) {
    return cockpit.spawn([TOOL].concat(args), { superuser: "require", err: "message" });
}

function fmtBytes(n) {
    if (n === undefined || n === null) return "-";
    const u = ["B", "KB", "MB", "GB", "TB", "PB"];
    let i = 0;
    while (n >= 1000 && i < u.length - 1) { n /= 1000; i++ }
    return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i];
}

function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
}

function badge(text, kind) {
    const b = el("span", "badge " + (kind || ""), text);
    return b;
}

function setBadge(node, text, kind) {
    node.textContent = text;
    node.className = "badge " + (kind || "");
}

/* ---------- theme: follow Cockpit's light/dark setting ---------- */
function applyTheme(style) {
    let s = style;
    if (!s) {
        try { s = localStorage.getItem("shell:style") } catch (e) { s = null }
    }
    const dark = s === "dark" ||
        ((!s || s === "auto") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
}
window.addEventListener("storage", ev => { if (ev.key === "shell:style") applyTheme() });
window.addEventListener("cockpit-style", ev => applyTheme(ev.detail && ev.detail.style));
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme());

/* ---------- rendering ---------- */
function renderPool(st) {
    const pool = st.pool;
    const mounted = st.branches.filter(b => b.mounted).length;
    const notMounted = st.branches.filter(b => !b.mounted);
    // mounted, but the running mergerfs does not use it
    const outside = pool.mounted ? st.branches.filter(b => b.mounted && !b.in_pool) : [];
    const missing = notMounted.concat(outside);

    if (!pool.mounted) setBadge($("pool-state"), "not mounted", "danger");
    else if (missing.length) setBadge($("pool-state"), "degraded", "danger");
    else if (!st.branches.length) setBadge($("pool-state"), "system disk only", "");
    else setBadge($("pool-state"), "ok", "ok");

    $("pool-usage").hidden = !pool.mounted;
    if (pool.mounted) {
        // with data disks, count only them: the system disk is mostly the OS
        const data = st.branches.filter(b => b.mounted && b.in_pool);
        const sum = k => data.reduce((a, b) => a + (b[k] || 0), 0);
        const size = data.length ? sum("size") : pool.size;
        const used = data.length ? sum("used") : pool.used;
        const avail = data.length ? sum("avail") : pool.avail;
        const pct = size ? Math.round(100 * used / size) : 0;
        $("pool-bar").style.width = pct + "%";
        $("pool-bar").classList.toggle("high", pct >= 90);
        $("pool-numbers").textContent = fmtBytes(used) + " used of " + fmtBytes(size) +
            " (" + pct + "%), " + fmtBytes(avail) + " free, " +
            (mounted ? mounted + " data disk" + (mounted === 1 ? "" : "s") : "system disk only");
    }
    $("pool-empty").hidden = st.branches.length > 0;

    const tb = $("branches").querySelector("tbody");
    tb.replaceChildren();
    // the system-disk branch, always first in the table
    const loc = st.local || {};
    const lr = el("tr");
    const ln = el("td");
    ln.append(el("strong", "", "system disk"));
    ln.append(el("div", "muted small", loc.path || ""));
    lr.append(ln);
    lr.append(el("td", "mono", st.system_disk || "-"));
    lr.append(el("td", "", "-"));
    lr.append(el("td", "", loc.has_files ? "has files" : "empty"));
    lr.append(el("td", "", fmtBytes(loc.system_free)));
    const ls = el("td");
    if (!loc.in_pool) ls.append(badge(pool.mounted ? "not in pool" : "-", pool.mounted ? "danger" : ""));
    else if (loc.mode === "RW") ls.append(badge("new files", "ok"));
    else ls.append(badge("old files only", ""));
    lr.append(ls);
    lr.append(el("td", ""));
    tb.append(lr);
    for (const b of st.branches) {
        const tr = el("tr");
        const name = el("td");
        name.append(el("strong", "", b.name));
        if (b.label) name.append(el("div", "muted small", b.label));
        tr.append(name);
        tr.append(el("td", "mono", b.mounted ? b.source : (b.fstab_source || "-")));
        tr.append(el("td", "", b.mounted ? fmtBytes(b.size) : "-"));
        tr.append(el("td", "", b.mounted ? fmtBytes(b.used) : "-"));
        tr.append(el("td", "", b.mounted ? fmtBytes(b.avail) : "-"));
        const s = el("td");
        if (b.mounted && (b.in_pool || !pool.mounted)) s.append(badge(pool.mounted ? "new files" : "mounted", pool.mounted ? "ok" : ""));
        else if (b.mounted) s.append(badge("not in pool", "danger"));
        else {
            s.append(badge("NOT MOUNTED", "danger"));
            if (b.stray) s.append(el("div", "danger-text small", "files on the system disk"));
        }
        tr.append(s);
        const act = el("td", "right");
        const btn = el("button", "btn danger-outline small-btn", "Remove");
        btn.type = "button";
        btn.addEventListener("click", () => askRemove(b));
        act.append(btn);
        tr.append(act);
        tb.append(tr);
    }

    // the 2026-10-24 incident: a branch silently missing from the pool
    const health = $("banner-health");
    if (missing.length) {
        health.hidden = false;
        health.replaceChildren();
        health.append(el("strong", "", "Disk missing from the pool: " + missing.map(b => b.name).join(", ") + ". "));
        health.append(document.createTextNode(notMounted.length
            ? "Its files are not visible until it is back; new files go to the other disks. Reconnect it (check cable and power): it is reattached automatically within seconds. Otherwise press Retry missing disks; if the disk is gone for good, press Remove on its row."
            : "The disk is mounted but the pool does not use it yet. Press Retry missing disks."));
    } else if (!pool.mounted) {
        health.hidden = false;
        health.replaceChildren();
        health.append(el("strong", "", "The pool is not mounted. "));
        health.append(document.createTextNode("Media and photos are unavailable. Press Retry missing disks; if it fails, check: systemctl status egos-pool"));
    } else {
        health.hidden = true;
    }
}

function renderStack(st) {
    const dl = $("stack");
    dl.replaceChildren();
    const rows = [
        ["Media (Jellyfin, Samba)", st.stack.JELLYFIN_MEDIA_LOCATION],
        ["Immich photos", st.stack.UPLOAD_LOCATION],
        ["Immich database", st.stack.DB_DATA_LOCATION],
        ["Samba share \\\\egocenter\\media", st.samba_media_path],
    ];
    for (const [k, v] of rows) {
        dl.append(el("dt", "", k));
        const dd = el("dd");
        if (!v) dd.append(el("span", "muted", "not configured yet"));
        else {
            dd.append(el("code", "", v));
            const onPool = v.replace(/\/$/, "").startsWith(st.pool.path);
            dd.append(document.createTextNode(" "));
            if (onPool && !st.pool.mounted) dd.append(badge("pool NOT MOUNTED", "danger"));
            else dd.append(badge(onPool ? "pool" : "system disk", onPool ? "ok" : ""));
        }
        dl.append(dd);
    }
}

function renderDisks(all) {
    // disks already in the pool are listed in the Pool card
    const disks = all.filter(d => !(d.mountpoints || "").split(" ").some(m => m.indexOf("/srv/egos/disks/") === 0));
    const tb = $("disks").querySelector("tbody");
    tb.replaceChildren();
    $("disks-empty").hidden = disks.length > 0;
    $("disks").hidden = !disks.length;
    for (const d of disks) {
        const tr = el("tr");
        tr.append(el("td", "mono", d.name));
        tr.append(el("td", "", d.size));
        tr.append(el("td", "", d.tran === "-" ? "" : d.tran));
        tr.append(el("td", "", d.model === "-" ? "" : d.model));
        tr.append(el("td", "small", d.mountpoints || ""));
        const act = el("td", "right");
        if (d.mountpoints) {
            act.append(el("span", "muted small", "in use: unmount it first"));
        } else {
            const btn = el("button", "btn primary small-btn", "Add to pool");
            btn.type = "button";
            btn.addEventListener("click", () => askAdd(d));
            act.append(btn);
        }
        tr.append(act);
        tb.append(tr);
    }
}

function renderJob(st, log) {
    const j = st.job;
    let kind = "", text = "idle";
    const m = log && log.match(/== END rc=(\d+)/);
    if (j.running) { kind = "busy"; text = "running" }
    else if (m) { kind = m[1] === "0" ? "ok" : "danger"; text = m[1] === "0" ? "finished" : "failed (" + m[1] + ")" }
    setBadge($("job-state"), text, kind);
    if (log !== null) {
        // rsync/dd progress uses \r: keep only the last update of each line
        const clean = log.split("\n").map(l => l.split("\r").pop()).join("\n");
        const pre = $("job-log");
        const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
        pre.textContent = clean || "No operation yet.";
        if (atEnd || j.running) pre.scrollTop = pre.scrollHeight;
    }
    for (const b of document.querySelectorAll("button.btn")) {
        if (b.id === "btn-refresh") continue;
        if (b.closest("dialog")) continue;
        b.disabled = j.running;
    }
    // legacy installs only (stack created outside the pool, before EgOS 4.2)
    const onPool = v => !!v && v.startsWith(st.pool.path);
    const legacy = !!st.stack.env && !(onPool(st.stack.JELLYFIN_MEDIA_LOCATION) && onPool(st.stack.UPLOAD_LOCATION));
    $("btn-link").hidden = !legacy;
    $("btn-link").disabled = j.running || !st.pool.mounted;

    const diskIn = st.branches.some(b => b.mounted && b.in_pool);
    const loc = st.local || {};
    $("btn-drain").disabled = j.running || !diskIn || !loc.has_files;
    $("btn-drain").title = !diskIn ? "Add a data disk first"
        : !loc.has_files ? "Nothing left on the system disk" : "";
    const problem = !st.pool.mounted || st.branches.some(b => !b.mounted || !b.in_pool);
    $("btn-reattach").disabled = j.running || !problem;
    $("btn-reattach").title = problem ? "" : "All disks are present";
    $("banner-held").hidden = !st.jellyfin_held;
}

/* ---------- refresh loop ---------- */
async function refresh() {
    clearTimeout(timer);
    let st, disks, log = null;
    try {
        st = JSON.parse(await run(["status", "--json"]));
        disks = JSON.parse(await run(["disks", "--json"]));
        if (st.job.has_log)
            log = await cockpit.spawn(["tail", "-c", "40000", st.job.log], { superuser: "require", err: "message" });
    } catch (e) {
        const denied = e && (e.problem === "access-denied" || e.problem === "not-authorized");
        $("banner-admin").hidden = !denied;
        $("banner-error").hidden = denied;
        if (!denied) $("banner-error").textContent = "Cannot read the storage state: " + (e.message || e.problem || e);
        $("content").hidden = true;
        timer = setTimeout(refresh, POLL_IDLE);
        return;
    }
    $("banner-admin").hidden = true;
    $("banner-error").hidden = true;
    $("content").hidden = false;
    state = st;
    renderPool(st);
    renderStack(st);
    renderDisks(disks);
    renderJob(st, log);
    if (lastJobRunning && !st.job.running) $("job-log").scrollIntoView({ block: "nearest" });
    lastJobRunning = st.job.running;
    timer = setTimeout(refresh, st.job.running ? POLL_JOB : POLL_IDLE);
}

async function startJob(args) {
    try {
        await run(["job"].concat(args));
    } catch (e) {
        $("banner-error").hidden = false;
        $("banner-error").textContent = "Could not start: " + (e.message || e.problem || e);
    }
    lastJobRunning = true;
    $("job-log").scrollIntoView({ behavior: "smooth", block: "nearest" });
    refresh();
}

/* ---------- dialogs ---------- */
function dialog({ title, body, okText, okKind, confirmWord, checkLabel }) {
    return new Promise(resolve => {
        const dlg = $("dlg");
        $("dlg-title").textContent = title;
        const b = $("dlg-body");
        b.replaceChildren();
        for (const p of body) {
            const n = el("p", p.kind || "", p.text);
            b.append(n);
        }
        $("dlg-check-row").hidden = !checkLabel;
        $("dlg-check").checked = false;
        $("dlg-check-label").textContent = checkLabel || "";
        const inp = $("dlg-confirm");
        $("dlg-confirm-row").hidden = !confirmWord;
        $("dlg-confirm-word").textContent = confirmWord || "";
        inp.value = "";
        const ok = $("dlg-ok");
        ok.textContent = okText;
        ok.className = "btn " + (okKind || "primary");
        const check = () => { ok.disabled = !!confirmWord && inp.value !== confirmWord };
        inp.oninput = check;
        check();
        dlg.onclose = () => resolve(dlg.returnValue === "ok"
            ? { checked: $("dlg-check").checked, typed: inp.value } : null);
        dlg.returnValue = "";
        dlg.showModal();
        if (confirmWord) inp.focus();
    });
}

async function askAdd(d) {
    const r = await dialog({
        title: "Add " + d.name + " to the pool",
        body: [
            { text: "Everything on " + d.name + " (" + d.size + (d.model !== "-" ? ", " + d.model : "") + ") will be ERASED.", kind: "danger-text" },
            { text: "The disk is partitioned, formatted as ext4, tested with a write/read check and joined to the pool. Services keep running." },
        ],
        okText: "Erase and add", okKind: "danger", confirmWord: d.name,
    });
    if (r) startJob(["add", "--disk", d.name, "--confirm", r.typed]);
}

async function askRemove(b) {
    const body = [
        { text: "The files on " + b.name + " disappear from the pool. The disk is NOT formatted." },
        { text: b.mounted ? "Keep the disk plugged in until the page says it can be unplugged."
                          : "The disk is not mounted: only the configuration is cleaned up." },
    ];
    const others = state.branches.filter(x => x.name !== b.name).length;
    if (!others)
        body.push({ text: "This is the last data disk: new files will go to the system disk again.", kind: "danger-text" });
    if (b.mounted)
        body.push({ text: "Files that exist only on this disk become unavailable to Jellyfin, Immich and Samba." });
    const r = await dialog({ title: "Remove " + b.name, body, okText: "Remove", okKind: "danger", confirmWord: b.name });
    if (r) startJob(["remove", "--branch", b.name, "--confirm", r.typed]);
}

async function askLink() {
    const r = await dialog({
        title: "Move data to the pool",
        body: [
            { text: "Copies the media folder onto the pool, checks the copy, then switches Jellyfin and the Samba share to it. The originals are kept on the system disk (*.pre-pool) until you delete them." },
            { text: "Jellyfin and Immich are stopped during the copy. Large libraries take hours: you can close this page." },
        ],
        okText: "Move", checkLabel: "Also move the Immich photo originals",
    });
    if (r) startJob(["link", "--yes"].concat(r.checked ? ["--immich"] : []));
}

async function askReattach() {
    const r = await dialog({
        title: "Retry missing disks",
        body: [{ text: "Mounts the disks that are back (check cable and power first) and adds them to the pool without stopping anything. Jellyfin restarts when every disk is present." }],
        okText: "Retry",
    });
    if (r) startJob(["reattach"]);
}

async function askDrain() {
    const r = await dialog({
        title: "Empty the system disk",
        body: [
            { text: "Moves the media and photos still on the system disk to the data disks. Paths do not change: Jellyfin, Immich and Samba keep working during the move." },
            { text: "Files changed in the last 10 minutes are left for a later run. Large libraries take hours: you can close this page." },
        ],
        okText: "Move files",
    });
    if (r) startJob(["drain"]);
}

document.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    $("btn-refresh").addEventListener("click", refresh);
    $("btn-link").addEventListener("click", askLink);
    $("btn-reattach").addEventListener("click", askReattach);
    $("btn-drain").addEventListener("click", askDrain);
    refresh();
    cockpit.transport.wait(() => document.body.removeAttribute("hidden"));
});
