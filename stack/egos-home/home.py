#!/usr/bin/env python3
"""EgOS home: pagina con i servizi del box.

Le schede vengono dai container Docker (letti tramite un docker-socket-proxy
in sola lettura) più una piccola lista fissa per i servizi che non sono
container (Cockpit). Una scheda per container: la porta è quella indicata
dalla label egos.home.port oppure la prima porta pubblicata che risponde come
pagina web (HTTP o HTTPS). Database, BitTorrent ecc. non rispondono in HTTP e
quindi non compaiono.

Label facoltative sui container (tutte egos.home.*):
  enable=false   nasconde il container
  name, desc     nome e descrizione della scheda
  port, scheme   porta e schema (http/https) da usare
  path           percorso (predefinito /)
  url            link completo al posto di host:porta (es. un dominio pubblico)
  icon           nome del file in icons/ senza .svg
  order          ordinamento (numero, predefinito 50)

Gira con network_mode: host, quindi prova le stesse porte del sistema.
Solo libreria standard.
"""
import html
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.getenv("PORT", "8000"))
ICONS = Path(__file__).with_name("icons")
FALLBACK_HOST = os.getenv("FALLBACK_HOST", "egocenter.local")
DOCKER_API = os.getenv("DOCKER_API", "http://127.0.0.1:23750").rstrip("/")
PROBE_TTL = 600          # secondi: quanto vale il risultato della prova di una porta
LABEL = "egos.home."

# servizi che non sono container: id, nome, descrizione, porta, schema, percorso
STATIC = [
    ("cockpit", "Cockpit", "Amministrazione", 9090, "https", "/"),
]

# valori predefiniti per le immagini note (si cercano nel nome dell'immagine):
# chiave, id/icona, nome, descrizione, ordine
KNOWN = [
    ("immich-server",   "immich",          "Immich",          "Foto e video",         10),
    ("jellyfin",        "jellyfin",        "Jellyfin",        "Film, serie e musica", 20),
    ("qbittorrent",     "qbittorrent",     "qBittorrent",     "Download torrent",     40),
    ("torrent-web",     "torrent-web",     "Ricerca torrent", "Cerca e scarica",      41),
    ("bentopdf",        "bentopdf",        "BentoPDF",        "Strumenti PDF",        50),
    ("multi-scrobbler", "multi-scrobbler", "Multi-scrobbler", "Scrobbling musica",    60),
]

HOST_RE = re.compile(r"^(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(:\d+)?$")
ID_RE = re.compile(r"[^a-z0-9-]+")

_probe_cache = {}        # porta -> (schema o None, istante)
_known_ports = {}        # nome container -> (porta, schema) visti quando era acceso
_lock = threading.Lock()


# --- prove di rete -----------------------------------------------------------
def is_open(port, timeout=0.4):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout):
            return True
    except OSError:
        return False


def _speaks_http(port, tls, timeout=1.0):
    req = b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout) as raw:
            raw.settimeout(timeout)
            if tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with ctx.wrap_socket(raw, server_hostname="localhost") as s:
                    s.sendall(req)
                    return s.recv(5) == b"HTTP/"
            raw.sendall(req)
            return raw.recv(5) == b"HTTP/"
    except (OSError, ssl.SSLError):
        return False


def web_scheme(port):
    """'http', 'https' o None se la porta non risponde come pagina web."""
    now = time.monotonic()
    with _lock:
        hit = _probe_cache.get(port)
    if hit and now - hit[1] < PROBE_TTL:
        return hit[0]
    scheme = "http" if _speaks_http(port, False) else ("https" if _speaks_http(port, True) else None)
    with _lock:
        _probe_cache[port] = (scheme, now)
    return scheme


# --- Docker ------------------------------------------------------------------
def docker_containers():
    """Elenco dei container dal proxy; None se il proxy non risponde."""
    try:
        with urllib.request.urlopen(DOCKER_API + "/containers/json?all=1", timeout=3) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


def known_for(image):
    img = image.lower()
    for key, sid, name, desc, order in KNOWN:
        if key in img:
            return sid, name, desc, order
    return None


def card_from_container(c):
    labels = c.get("Labels") or {}
    lab = {k[len(LABEL):]: v for k, v in labels.items() if k.startswith(LABEL)}
    if lab.get("enable", "").lower() in ("false", "0", "no"):
        return None
    cname = (c.get("Names") or ["/?"])[0].lstrip("/")
    running = c.get("State") == "running"
    status = c.get("Status", "")
    known = known_for(c.get("Image", ""))

    port = scheme = None
    if lab.get("port", "").isdigit():
        port = int(lab["port"])
        scheme = lab.get("scheme") or (web_scheme(port) if running else None) or "http"
    elif running:
        ports = sorted({p["PublicPort"] for p in c.get("Ports") or []
                        if p.get("Type") == "tcp" and p.get("PublicPort")
                        and p.get("IP", "0.0.0.0") in ("0.0.0.0", "::", "")})
        for p in ports:
            s = web_scheme(p)
            if s:
                port, scheme = p, lab.get("scheme") or s
                break
    if port is None and lab.get("url") is None:
        # un container fermo non pubblica porte: si usa quella vista quando era acceso
        with _lock:
            seen = _known_ports.get(cname)
        if not seen:
            return None
        port, scheme = seen
    elif port is not None and running:
        with _lock:
            _known_ports[cname] = (port, scheme)

    sid = ID_RE.sub("-", (lab.get("icon") or (known[0] if known else cname)).lower()).strip("-")
    order = lab.get("order", "")
    return {
        "id": "c-" + ID_RE.sub("-", cname.lower()),
        "icon": sid,
        "name": lab.get("name") or (known[1] if known else cname),
        "desc": lab.get("desc") or (lab["url"].split("://", 1)[-1].split("/", 1)[0] if lab.get("url")
                                    else known[2] if known else c.get("Image", "").split("@")[0]),
        "order": int(order) if order.lstrip("-").isdigit() else (known[3] if known else 50),
        "port": port,
        "scheme": scheme or "http",
        "path": lab.get("path") or "/",
        "url": lab.get("url"),
        "state": "on" if running and "(unhealthy)" not in status else ("warn" if running else "off"),
    }


def services():
    """(schede, docker_ok): container + servizi fissi, ordinati."""
    cs = docker_containers()
    cards = []
    if cs is not None:
        with ThreadPoolExecutor(8) as ex:
            cards = [x for x in ex.map(card_from_container, cs) if x]
    with ThreadPoolExecutor(len(STATIC)) as ex:
        opens = list(ex.map(lambda s: is_open(s[3]), STATIC))
    for (sid, name, desc, port, scheme, path), up in zip(STATIC, opens):
        cards.append({"id": "s-" + sid, "icon": sid, "name": name, "desc": desc, "order": 90,
                      "port": port, "scheme": scheme, "path": path, "url": None,
                      "state": "on" if up else "off"})
    cards.sort(key=lambda x: (x["order"], x["name"].lower()))
    return cards, cs is not None


# --- pagina ------------------------------------------------------------------
def request_host(header):
    m = HOST_RE.match(header or "")
    return m.group(1) if m else FALLBACK_HOST


def icon_html(card):
    if (ICONS / (card["icon"] + ".svg")).is_file():
        return f'<img src="/icons/{card["icon"]}.svg" alt="" width="40" height="40">'
    # nessuna icona: iniziale su un colore ricavato dal nome
    hue = sum(map(ord, card["name"])) * 37 % 360
    letter = html.escape(card["name"][:1].upper() or "?")
    return (f'<svg class="auto" viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">'
            f'<rect width="40" height="40" rx="9" fill="hsl({hue} 55% 45%)"/>'
            f'<text x="20" y="27" text-anchor="middle" font-size="20" font-weight="600" '
            f'fill="#fff" font-family="system-ui, sans-serif">{letter}</text></svg>')


def render(host):
    cards, docker_ok = services()
    up = sum(c["state"] != "off" for c in cards)
    out = []
    for c in cards:
        url = c["url"] or f'{c["scheme"]}://{host}:{c["port"]}{c["path"]}'
        tail = "↗" if c["url"] else f':{c["port"]}'
        off = c["state"] == "off"
        out.append(
            f'<a class="card {c["state"]}" id="{c["id"]}" href="{html.escape(url)}"'
            f'{" aria-disabled=\"true\" tabindex=\"-1\"" if off else ""}>'
            f'{icon_html(c)}'
            f'<span class="txt"><span class="name">{html.escape(c["name"])}</span>'
            f'<span class="desc">{html.escape(c["desc"])}</span></span>'
            f'<span class="port">{tail}</span></a>'
        )
    warn = "" if docker_ok else '<p class="banner">Docker non raggiungibile: mostro solo i servizi fissi.</p>'
    return PAGE.format(host=html.escape(host), up=up, total=len(cards), cards="\n".join(out),
                       banner=warn, ids=html.escape(json.dumps(sorted(c["id"] for c in cards))))


def api_status():
    cards, docker_ok = services()
    return {"docker": docker_ok, "services": {c["id"]: c["state"] for c in cards}}


class Handler(BaseHTTPRequestHandler):
    server_version = "egos-home"

    def send(self, code, ctype, body, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = render(request_host(self.headers.get("Host"))).encode()
            self.send(200, "text/html; charset=utf-8", body)
        elif path == "/api/status":
            self.send(200, "application/json", json.dumps(api_status()).encode())
        elif path.startswith("/icons/") and re.fullmatch(r"/icons/[a-z0-9-]+\.svg", path):
            f = ICONS / path[len("/icons/"):]
            if f.is_file():
                self.send(200, "image/svg+xml", f.read_bytes(), "max-age=86400")
            else:
                self.send(404, "text/plain", b"not found")
        elif path == "/health":
            self.send(200, "text/plain", b"ok")
        else:
            self.send(404, "text/plain", b"not found")

    def log_message(self, fmt, *args):
        pass


PAGE = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EgOS Home</title>
<style>
:root {{
  --bg: #f4f5f7; --card: #ffffff; --border: #dfe2e7; --text: #1b1f24;
  --muted: #5f6b7a; --accent: #2f6fd6; --ok: #1f9d55; --warn: #b7791f; --shadow: 0 1px 2px rgba(0,0,0,.06);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #14171c; --card: #1d2229; --border: #2c333d; --text: #e6e9ee;
    --muted: #96a1b0; --accent: #6ea2ff; --ok: #3ccf7e; --warn: #e0a84a; --shadow: none;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #14171c; --card: #1d2229; --border: #2c333d; --text: #e6e9ee;
  --muted: #96a1b0; --accent: #6ea2ff; --ok: #3ccf7e; --warn: #e0a84a; --shadow: none;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}}
main {{ max-width: 960px; margin: 0 auto; padding: 28px 16px; }}
header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 18px; }}
h1 {{ font-size: 20px; margin: 0; font-weight: 650; }}
h1 small {{ color: var(--muted); font-weight: 400; font-size: 14px; margin-left: 6px; }}
.count {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
.count b {{ color: var(--ok); }}
.banner {{ margin: 0 0 14px; padding: 8px 12px; border: 1px solid var(--warn); border-radius: 8px; color: var(--warn); font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 10px; }}
.card {{
  display: flex; align-items: center; gap: 12px; padding: 10px 12px;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  color: inherit; text-decoration: none; box-shadow: var(--shadow);
  transition: border-color .15s, transform .15s, opacity .2s, filter .2s;
}}
.card:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
.card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
.card img, .card svg.auto {{ flex: none; width: 40px; height: 40px; object-fit: contain; }}
.txt {{ display: flex; flex-direction: column; min-width: 0; flex: 1; }}
.name {{ font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.desc {{ color: var(--muted); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.port {{ color: var(--muted); font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }}
.card.off {{ opacity: .35; filter: grayscale(1); pointer-events: none; box-shadow: none; }}
.card.off .port::after {{ content: " · spento"; font-family: system-ui, sans-serif; }}
.card.warn {{ border-color: var(--warn); }}
.card.warn .port::after {{ content: " · non sano"; color: var(--warn); font-family: system-ui, sans-serif; }}
@media (max-width: 460px) {{
  .grid {{ grid-template-columns: 1fr 1fr; gap: 8px; }}
  .card {{ flex-direction: column; text-align: center; gap: 6px; padding: 12px 8px; }}
  .txt {{ align-items: center; width: 100%; }}
  .card.off .port::after, .card.warn .port::after {{ content: ""; }}
}}
</style>
</head>
<body>
<main>
  <header>
    <h1>EgOS<small>{host}</small></h1>
    <span class="count"><b id="up">{up}</b> attivi su {total}</span>
  </header>
  {banner}
  <nav class="grid" data-ids="{ids}">
{cards}
  </nav>
</main>
<script>
const grid = document.querySelector(".grid");
const ids = JSON.parse(grid.dataset.ids);
setInterval(async () => {{
  try {{
    const st = await (await fetch("/api/status", {{cache: "no-store"}})).json();
    const now = Object.keys(st.services).sort();
    // container comparsi o spariti, o Docker tornato/perso: si ricarica la pagina
    if (JSON.stringify(now) !== JSON.stringify(ids) || (!st.docker) !== !!document.querySelector(".banner")) {{
      location.reload(); return;
    }}
    let up = 0;
    for (const [id, state] of Object.entries(st.services)) {{
      const el = document.getElementById(id);
      if (!el) continue;
      if (state !== "off") up++;
      el.classList.remove("on", "off", "warn");
      el.classList.add(state);
      if (state === "off") {{ el.setAttribute("aria-disabled", "true"); el.setAttribute("tabindex", "-1"); }}
      else {{ el.removeAttribute("aria-disabled"); el.removeAttribute("tabindex"); }}
    }}
    document.getElementById("up").textContent = up;
  }} catch (e) {{}}
}}, 15000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(f"egos-home in ascolto su :{PORT}, Docker via {DOCKER_API}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
