#!/usr/bin/env python3
"""EgoBuilder generator: profile (JSON) -> customised copy of the EgOS repo tree.

The build scripts read every file EgOS adds to Debian from the repo tree
(rootfs/ stack/ webapp/ build/...). The generator copies that tree to a staging
directory and rewrites the files a profile touches; the build then runs on the
staged copy exactly as `make iso-amd64` runs on the repo.

Every edit of an existing file is anchored: if the anchor is not found exactly
once the generator stops (GenError) instead of producing a half-patched image,
the same "every assumption checked" rule the build scripts follow.

    python3 egobuilder/generator.py PROFILE.json OUT_DIR    # stage + tools/check.sh
    python3 egobuilder/generator.py --default > profile.json
"""
import copy
import difflib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CATALOG_DIR = os.path.join(HERE, "catalog")


class GenError(Exception):
    pass


# --------------------------------------------------------------------------
# Languages, keyboards
# --------------------------------------------------------------------------
# locale -> defaults for the rest of the system and for the apps
LOCALES = {
    "it_IT": {"label": "Italiano", "keyboard": "it", "timezone": "Europe/Rome", "country": "IT",
              "lang2": "it", "lang3": "ita", "jellyfin": "it"},
    "en_US": {"label": "English (US)", "keyboard": "us", "timezone": "America/New_York", "country": "US",
              "lang2": "en", "lang3": "eng", "jellyfin": "en-US"},
    "en_GB": {"label": "English (UK)", "keyboard": "gb", "timezone": "Europe/London", "country": "GB",
              "lang2": "en", "lang3": "eng", "jellyfin": "en-GB"},
    "de_DE": {"label": "Deutsch", "keyboard": "de", "timezone": "Europe/Berlin", "country": "DE",
              "lang2": "de", "lang3": "deu", "jellyfin": "de"},
    "fr_FR": {"label": "Français", "keyboard": "fr", "timezone": "Europe/Paris", "country": "FR",
              "lang2": "fr", "lang3": "fra", "jellyfin": "fr"},
    "es_ES": {"label": "Español", "keyboard": "es", "timezone": "Europe/Madrid", "country": "ES",
              "lang2": "es", "lang3": "spa", "jellyfin": "es"},
    "pt_PT": {"label": "Português", "keyboard": "pt", "timezone": "Europe/Lisbon", "country": "PT",
              "lang2": "pt", "lang3": "por", "jellyfin": "pt-PT"},
    "pt_BR": {"label": "Português (Brasil)", "keyboard": "br", "timezone": "America/Sao_Paulo", "country": "BR",
              "lang2": "pt", "lang3": "por", "jellyfin": "pt-BR"},
    "nl_NL": {"label": "Nederlands", "keyboard": "nl", "timezone": "Europe/Amsterdam", "country": "NL",
              "lang2": "nl", "lang3": "nld", "jellyfin": "nl"},
    "pl_PL": {"label": "Polski", "keyboard": "pl", "timezone": "Europe/Warsaw", "country": "PL",
              "lang2": "pl", "lang3": "pol", "jellyfin": "pl"},
}
KEYBOARDS = {
    "it": "Italian", "us": "US", "gb": "UK", "de": "German", "fr": "French",
    "es": "Spanish", "pt": "Portuguese", "br": "Brazilian", "nl": "Dutch", "pl": "Polish",
    "ch": "Swiss", "be": "Belgian", "latam": "Latin American",
}
PARTITIONING = {"atomic": "Everything in one partition", "home": "Separate /home", "multi": "Separate /home, /var, /tmp"}
BASE_MEDIA = ["Films", "Musics", "SerieTv", "ROMs", "downloads"]   # always created: Samba share content
RESERVED_PORTS = {
    ("tcp", 22): "SSH", ("tcp", 80): "setup page (1st boot)", ("tcp", 139): "Samba",
    ("tcp", 445): "Samba", ("tcp", 9090): "Cockpit", ("udp", 53): "DNS",
    ("udp", 5353): "mDNS (avahi)", ("udp", 3702): "WS-Discovery", ("udp", 137): "Samba", ("udp", 138): "Samba",
}
RESERVED_USERS = {"root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp",
                  "proxy", "www-data", "backup", "list", "irc", "nobody", "systemd-network", "messagebus",
                  "sshd", "avahi", "docker", "sudo", "admin", "cockpit-ws", "dnsmasq", "postgres", "redis"}


# --------------------------------------------------------------------------
# Catalog: one file per app, catalog/<id>.app = JSON metadata, then sections
# "--- compose", "--- env", "--- wizard-wait|setup|verify" (text kept verbatim)
# --------------------------------------------------------------------------
def parse_app(text, app_id):
    parts = re.split(r"^--- ([a-z-]+)\n", text, flags=re.M)
    try:
        meta = json.loads(parts[0])
    except ValueError as e:
        raise GenError(f"catalog/{app_id}.app: invalid metadata JSON: {e}")
    meta["id"] = app_id
    meta["sections"] = {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    for s in ("compose", "env"):
        if s not in meta["sections"]:
            raise GenError(f"catalog/{app_id}.app: missing section --- {s}")
    meta.setdefault("ports", {})
    for k in ("pool_dirs", "home_dirs", "named_volumes", "stage_files", "stage_dirs", "config_files"):
        meta.setdefault(k, [])
    for k in ("secrets", "options", "wizard_order"):
        meta.setdefault(k, {})
    meta["services"] = service_names(meta["sections"]["compose"])
    return meta


def load_catalog():
    cat = {}
    for f in sorted(os.listdir(CATALOG_DIR)):
        if f.endswith(".app"):
            app_id = f[:-4]
            with open(os.path.join(CATALOG_DIR, f)) as fh:
                cat[app_id] = parse_app(fh.read(), app_id)
    # stock apps first, in the order of the stock compose file
    order = ["immich", "jellyfin", "egos-home"]
    return {k: cat[k] for k in sorted(cat, key=lambda k: (order.index(k) if k in order else 99, k))}


def service_names(compose):
    return re.findall(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):[ \t]*$", compose, flags=re.M)


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
def default_profile(catalog=None):
    catalog = catalog or load_catalog()
    apps = {}
    for app_id, a in catalog.items():
        apps[app_id] = {
            # the stock stack: Immich, Jellyfin and the recommended apps (EgosHome)
            "enabled": app_id in ("immich", "jellyfin") or bool(a.get("recommended")),
            "version": a.get("default_version", "latest"),
            "ports": {k: p["default"] for k, p in a["ports"].items()},
            "secrets": {k: "" for k in a["secrets"]},          # "" = random, generated on the box
            "options": {k: o.get("default", "") for k, o in a["options"].items()},
        }
    return {
        "profile_name": "egocenter",
        "system": {"locale": "it_IT", "keyboard": "it", "timezone": "Europe/Rome",
                   "hostname": "egocenter", "domain": "lan", "server_label": "EgoCenter"},
        "accounts": {"root_password": "", "lock_root": False,
                     "user_mode": "firstboot", "user": "", "email": "", "password": ""},
        "setup": {"ssid": "SetupBox", "wifi_password": "setup1234", "wifi_country": "IT", "mdns_name": "setupbox"},
        "apps": apps,
        "custom_apps": [],
        "services": {"ssh": True, "ssh_password_auth": True, "ssh_keys": "", "cockpit": True,
                     "samba": True, "docker_log_limit": False, "auto_updates": False},
        "packages": {"extra": []},
        "install": {"partitioning": "atomic", "after_install": "poweroff"},
    }


def merge(base, over):
    """Profile from the UI/file over the defaults: unknown keys dropped, missing keys defaulted."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict) and k != "apps":
            out[k] = merge(out[k], v)
        elif k == "apps" and isinstance(v, dict):
            for app_id, av in v.items():
                if app_id in out["apps"] and isinstance(av, dict):
                    out["apps"][app_id] = merge(out["apps"][app_id], av)
        else:
            out[k] = v
    return out


def normalize(profile, catalog=None):
    catalog = catalog or load_catalog()
    p = merge(default_profile(catalog), profile)
    if isinstance(p["packages"]["extra"], str):
        p["packages"]["extra"] = p["packages"]["extra"].split()
    customs = []
    for c in p.get("custom_apps") or []:
        if not isinstance(c, dict):
            continue
        c = {"id": str(c.get("id", "")).strip(), "name": str(c.get("name", "")).strip(),
             "enabled": c.get("enabled", True) is not False,
             "compose": str(c.get("compose", "")), "env": str(c.get("env", "")),
             "pool_dirs": _list(c.get("pool_dirs")), "home_dirs": _list(c.get("home_dirs"))}
        customs.append(c)
    p["custom_apps"] = customs
    return p


def _list(v):
    if isinstance(v, str):
        v = re.split(r"[\s,]+", v)
    return [x.strip() for x in (v or []) if str(x).strip()]


def locale_info(p):
    return LOCALES.get(p["system"]["locale"], LOCALES["it_IT"])


def custom_as_app(c):
    """A custom app in the same shape as a catalog app."""
    compose = c["compose"].replace("\t", "  ").rstrip()
    lines = compose.split("\n")
    first = next((l for l in lines if l.strip()), "")
    if first and not first.startswith(" "):
        compose = "\n".join(("  " + l) if l.strip() else l for l in lines)
    env = c["env"].strip()
    env = f"# ---- {c['name'] or c['id']} ----\n" + (env + "\n" if env else "")
    ports = {}
    for i, (host, proto) in enumerate(re.findall(r"-\s*['\"]?(?:[\d.]+:)?(\d+):\d+(?:/(tcp|udp))?", compose)):
        ports[f"p{i}"] = {"default": int(host), "proto": proto or "tcp", "label": "port"}
    return {"id": c["id"], "name": c["name"] or c["id"], "headline": c["name"] or c["id"], "custom": True,
            "sections": {"compose": compose + "\n", "env": env}, "ports": ports,
            "pool_dirs": c["pool_dirs"], "home_dirs": c["home_dirs"], "named_volumes": [],
            "stage_files": [], "stage_dirs": [], "config_files": [], "secrets": {}, "options": {}, "wizard_order": {},
            "services": service_names(compose + "\n"), "homepage": {"description": "Custom app"}}


def selected_apps(p, catalog):
    """[(meta, settings)] in catalog order, then the custom apps."""
    out = [(catalog[a], p["apps"][a]) for a in catalog if p["apps"][a]["enabled"]]
    for c in p["custom_apps"]:
        if c["enabled"]:
            a = custom_as_app(c)
            out.append((a, {"ports": {k: v["default"] for k, v in a["ports"].items()},
                            "version": "", "secrets": {}, "options": {}}))
    return out


# --------------------------------------------------------------------------
# Validation: list of {"field": dotted path, "msg": text}; warnings separate
# --------------------------------------------------------------------------
RE_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
RE_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_TZ = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
RE_PKG = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
RE_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
RE_SECRET = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
RE_DIR = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")
RE_ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
RE_ENV = re.compile(r"^[A-Z_][A-Z0-9_]*=")


def validate(profile, catalog=None):
    catalog = catalog or load_catalog()
    p = normalize(profile, catalog)
    err, warn = [], []
    E = lambda f, m: err.append({"field": f, "msg": m})
    W = lambda f, m: warn.append({"field": f, "msg": m})
    s, acc, st, sv = p["system"], p["accounts"], p["setup"], p["services"]

    if s["locale"] not in LOCALES:
        E("system.locale", "unsupported language")
    if s["keyboard"] not in KEYBOARDS:
        E("system.keyboard", "unsupported keyboard layout")
    if not RE_TZ.match(s["timezone"]) or (os.path.isdir("/usr/share/zoneinfo") and
                                          not os.path.isfile(os.path.join("/usr/share/zoneinfo", s["timezone"]))):
        E("system.timezone", "unknown time zone (e.g. Europe/Rome)")
    if not RE_HOST.match(s["hostname"]):
        E("system.hostname", "hostname: lowercase letters, digits and '-' (max 63)")
    if s["domain"] == "local" or not re.match(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", s["domain"]):
        E("system.domain", "invalid domain ('local' is reserved for mDNS)")
    if not re.match(r"^[A-Za-z0-9 ._-]{1,40}$", s["server_label"]):
        E("system.server_label", "network name: letters, digits, spaces (max 40)")

    if len(acc["root_password"]) < 6:
        E("accounts.root_password", "root password: at least 6 characters")
    if acc["user_mode"] not in ("firstboot", "preset"):
        E("accounts.user_mode", "user mode: firstboot or preset")
    if acc["user_mode"] == "preset":
        if not RE_USER.match(acc["user"]) or acc["user"] in RESERVED_USERS:
            E("accounts.user", "invalid or reserved Linux user name")
        if not RE_EMAIL.match(acc["email"]):
            E("accounts.email", "invalid email")
        if len(acc["password"]) < 8:
            E("accounts.password", "user password: at least 8 characters")
        if "'" in acc["password"] or "\n" in acc["password"]:
            E("accounts.password", "the password cannot contain single quotes or newlines")
        W("accounts.user_mode", "the user password will be stored in clear text inside the image (/var/lib/setup-data.json): do not share the ISO")
    if not re.match(r"^[A-Za-z0-9 _-]{1,32}$", st["ssid"]):
        E("setup.ssid", "SSID: letters, digits, spaces (max 32)")
    if not (8 <= len(st["wifi_password"]) <= 63) or not st["wifi_password"].isprintable():
        E("setup.wifi_password", "setup Wi-Fi password: 8-63 characters")
    if not re.match(r"^[A-Z]{2}$", st["wifi_country"]):
        E("setup.wifi_country", "Wi-Fi country: 2 uppercase letters (e.g. IT)")
    if not RE_HOST.match(st["mdns_name"]) or st["mdns_name"] == s["hostname"]:
        E("setup.mdns_name", "invalid setup mDNS name, or the same as the hostname")
    if acc["user_mode"] == "firstboot" and st["wifi_password"] == "setup1234":
        W("setup.wifi_password", "default setup Wi-Fi password (setup1234)")

    if p["install"]["partitioning"] not in PARTITIONING:
        E("install.partitioning", "unknown partitioning scheme")
    if p["install"]["after_install"] not in ("poweroff", "reboot"):
        E("install.after_install", "after installation: poweroff or reboot")
    for pkg in p["packages"]["extra"]:
        if not RE_PKG.match(pkg):
            E("packages.extra", f"invalid package name: {pkg}")
    for i, line in enumerate(sv["ssh_keys"].splitlines()):
        if line.strip() and not re.match(r"^(ssh-(ed25519|rsa|dss)|ecdsa-sha2-\S+|sk-\S+) \S+", line.strip()):
            E("services.ssh_keys", f"line {i + 1}: does not look like an SSH public key")
    if sv["ssh"] and not sv["ssh_password_auth"] and not sv["ssh_keys"].strip():
        E("services.ssh_keys", "without SSH passwords you need at least one key, or you will be locked out")

    # apps
    used, envkeys, services = {}, {}, {}
    sel = selected_apps(p, catalog)
    for c in p["custom_apps"]:
        f = f"custom.{c['id'] or '?'}"
        if not RE_ID.match(c["id"]):
            E(f, "custom app id: lowercase letters, digits, '-' (2-31)")
        elif c["id"] in catalog:
            E(f, f"id '{c['id']}' is already used by the catalog")
        if c["enabled"] and not service_names(custom_as_app(c)["sections"]["compose"]):
            E(f, "compose: no service found (e.g. 'myapp:' followed by image:, ports: ...)")
        if "{{" in c["compose"] + c["env"]:
            E(f, "'{{' is not allowed in custom texts")
        for line in c["env"].splitlines():
            if line.strip() and not line.startswith("#") and not RE_ENV.match(line):
                E(f, f"invalid env line: {line[:40]}")
        for d in c["pool_dirs"] + c["home_dirs"]:
            if not RE_DIR.match(d) or ".." in d.split("/"):
                E(f, f"invalid folder: {d}")
    for a, cfg in sel:
        f = f"apps.{a['id']}" if not a.get("custom") else f"custom.{a['id']}"
        if not a.get("custom") and not RE_TAG.match(cfg["version"]):
            E(f + ".version", "invalid image tag")
        if not a.get("custom") and cfg["version"] in ("latest", "stable", "lts", "release"):
            W(f + ".version", f"{a['name']}: floating tag '{cfg['version']}', the build is not reproducible")
        for k, sec in cfg["secrets"].items():
            if sec and not RE_SECRET.match(sec):
                E(f + ".secrets." + k, "secret: 8-128 characters from A-Z a-z 0-9 . _ - (empty = random)")
            if sec == "password":
                W(f + ".secrets." + k, f"{a['name']}: default database password")
        for k, o in a["options"].items():
            if o.get("choices") and cfg["options"].get(k) not in o["choices"]:
                E(f + ".options." + k, "value not allowed")
            if not re.match(r"^[A-Za-z0-9 ._-]{1,40}$", str(cfg["options"].get(k, ""))):
                E(f + ".options." + k, "invalid value")
        for k, port in a["ports"].items():
            try:
                n = int(cfg["ports"].get(k, port["default"]))
            except (TypeError, ValueError):
                n = -1
            if port.get("fixed") and n != port["default"]:
                E(f + ".ports." + k, f"fixed port ({port['default']}): the app uses the host network")
            if not 1 <= n <= 65535:
                E(f + ".ports." + k, "port: 1-65535")
                continue
            for proto in port["proto"].split("+"):
                key = (proto, n)
                if key == ("tcp", 22) and not sv["ssh"]:
                    pass
                elif key in RESERVED_PORTS:
                    E(f + ".ports." + k, f"port {n}/{proto} is reserved for {RESERVED_PORTS[key]}")
                if key in used:
                    E(f + ".ports." + k, f"port {n}/{proto} is already used by {used[key]}")
                used[key] = a["name"]
        for line in a["sections"]["env"].splitlines():
            m = RE_ENV.match(line)
            if m:
                k = line.split("=", 1)[0]
                if k in envkeys and envkeys[k] != a["name"]:
                    E(f, f"variable {k} is also defined by {envkeys[k]}")
                envkeys[k] = a["name"]
        for svc in a["services"]:
            if svc in services:
                E(f, f"compose service '{svc}' is also defined by {services[svc]}")
            services[svc] = a["name"]
    if not sel:
        W("apps", "no apps: the image will only have the system, the disk pool, Samba and Cockpit")
    return {"errors": err, "warnings": warn}


# --------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------
def replace_once(text, old, new, where):
    n = text.count(old)
    if n != 1:
        raise GenError(f"{where}: anchor not found exactly once ({n}): {old[:70]!r}")
    return text.replace(old, new)


def sub_once(text, pattern, repl, where, flags=re.M):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != 1:
        raise GenError(f"{where}: pattern found {n} times: {pattern}")
    return new


def drop_check(text, needle, where):
    """Remove a 2-line `grep ... \\n    || { echo ...; exit 1; }` check of the build scripts."""
    return sub_once(text, r"^[^\n]*" + re.escape(needle) + r"[^\n]*\n[ \t]+\|\|[^\n]*\n", "", where)


def render(text, p, a, cfg, where):
    li = locale_info(p)

    def rep(m):
        key = m.group(1)
        if key == "version":
            return f"@{a['build_var']}@" if a.get("build_var") else cfg["version"]
        if key.startswith("port."):
            k = key[5:]
            if k not in a["ports"]:
                raise GenError(f"{where}: port {k} not declared")
            return str(int(cfg["ports"].get(k, a["ports"][k]["default"])))
        if key.startswith("secret."):
            return cfg["secrets"].get(key[7:]) or "__RANDOM__"
        if key.startswith("option."):
            return str(cfg["options"].get(key[7:], ""))
        if key == "hostname":
            return p["system"]["hostname"]
        if key == "tz":
            return p["system"]["timezone"]
        if key in ("lang2", "lang3"):
            return li[key]
        raise GenError(f"{where}: unknown placeholder {{{{{key}}}}}")
    return re.sub(r"\{\{([a-z0-9_.]+)\}\}", rep, text)


def app_url(p, a, cfg):
    if "web" not in a["ports"]:
        return None
    scheme = (a.get("homepage") or {}).get("scheme", "http")
    return f"{scheme}://{p['system']['hostname']}.local:{cfg['ports'].get('web', a['ports']['web']['default'])}"


# --------------------------------------------------------------------------
# Stack files (stack/)
# --------------------------------------------------------------------------
COMPOSE_HEAD = ("#\n# egos stack: {headlines}\n"
                "# Requires Docker Compose v2 (docker-compose-plugin, official Docker repo)\n")
WIZARD_HEAD = ('#!/bin/bash\n# mentality 0 errors\nset -e\n'
               'cd "$(dirname "$0")"   # the curl -d @file.json calls use relative paths\n\n'
               'echo "Starting compose"\ndocker compose -f docker-compose.yml up -d\n\n')


def gen_compose(p, sel):
    if not sel:
        return "#\n# egos stack: no apps selected (EgoBuilder)\n#\n\nname: egos\n\nservices: {}\n"
    out = COMPOSE_HEAD.format(headlines=" + ".join(a["headline"] for a, _ in sel))
    notes = "".join(a.get("compose_note", "") for a, _ in sel)
    out += (notes or "#\n") + "\nname: egos\n\nservices:\n\n"
    out += "\n".join(render(a["sections"]["compose"].rstrip("\n") + "\n", p, a, cfg, a["id"] + ".compose")
                     for a, cfg in sel)
    vols = [v for a, _ in sel for v in a["named_volumes"]]
    if vols:
        out += "\nvolumes:\n" + "".join(f"  {v}:\n" for v in dict.fromkeys(vols))
    return out


def gen_env(p, sel, stock_env):
    head = stock_env.split("# ---- General ----")[0]
    out = head + "# ---- General ----\nTZ=" + p["system"]["timezone"] + "\n"
    for a, cfg in sel:
        out += "\n" + render(a["sections"]["env"], p, a, cfg, a["id"] + ".env")
    return out


def gen_wizard(p, sel):
    if not sel:
        return '#!/bin/bash\n# EgoBuilder: no apps selected, nothing to deploy\necho "ALL OK"\n'
    out = WIZARD_HEAD

    def phase(name):
        apps = [(a, c) for a, c in sel if f"wizard-{name}" in a["sections"]]
        apps.sort(key=lambda ac: ac[0]["wizard_order"].get(name, 99))
        return "".join(render(a["sections"][f"wizard-{name}"], p, a, c, a["id"] + ".wizard") for a, c in apps)
    waits = phase("wait")
    if waits:
        out += waits + 'echo "Server up"\n\n'
    out += phase("setup") + phase("verify") + 'echo "ALL OK"\n'
    return out


def gen_jellsrv(p, cfg):
    li = locale_info(p)
    return ("{\n"
            f'  "ServerName":{json.dumps(cfg["options"].get("server_name") or "EgoFin")},\n'
            f'  "UICulture":"{li["jellyfin"]}",\n'
            f'  "MetadataCountryCode":"{li["country"]}",\n'
            f'  "PreferredMetadataLanguage":"{li["lang2"]}"\n'
            "}\n")


def gen_homepage_services(p, sel):
    host = p["system"]["hostname"]
    out = "# Generated by EgoBuilder: the apps of this image. Edit freely, it is not overwritten.\n- EgOS:\n"
    if p["services"]["cockpit"]:
        out += (f"    - Cockpit:\n        href: https://{host}.local:9090\n"
                "        description: Control panel and disks\n        icon: cockpit.png\n")
    for a, cfg in sel:
        hp = a.get("homepage")
        url = app_url(p, a, cfg)
        if hp is None or not url:
            continue
        out += f"    - {a['name']}:\n        href: {url}\n        description: {hp.get('description', '')}\n"
        if hp.get("icon"):
            out += f"        icon: {hp['icon']}\n"
    return out


# --------------------------------------------------------------------------
# dirtree.sh (2nd boot): folders, stack files, credentials, .env, Samba
# --------------------------------------------------------------------------
SECRETS_PY = r'''
# EgoBuilder: fill the .env placeholders. __RANDOM__ values are generated once and
# kept in /var/lib/egos/stack-secrets.json, so a retry at the next boot reuses them
# (a database initialised with the first password keeps working).
python3 - "$H/app/repos/.env" <<'PY'
import json, os, secrets, sys
env, store = sys.argv[1], "/var/lib/egos/stack-secrets.json"
d = json.load(open("/var/lib/setup-data.json"))
s = json.load(open(store)) if os.path.exists(store) else {}
tok = {"__USER__": d["user"], "__PASSWORD__": d["password"], "__EMAIL__": d["email"]}
out = []
for line in open(env).read().split("\n"):
    k, sep, v = line.partition("=")
    if sep and v == "__RANDOM__":
        v = s.setdefault(k, secrets.token_hex(16))
        line = k + "=" + v
    elif sep and v in tok:
        v = tok[v]
        line = k + "=" + ("'" + v + "'" if "'" not in v else v)
    out.append(line)
os.makedirs(os.path.dirname(store), exist_ok=True)
with open(os.open(store, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump(s, f)
open(env, "w").write("\n".join(out))
PY
chmod 600 "$H/app/repos/.env"
'''


def gen_dirtree(text, p, sel):
    W = "rootfs/usr/local/bin/dirtree.sh"
    # folders
    start = text.index('mkdir -p "$H/app/repos"\n') if text.count('mkdir -p "$H/app/repos"\n') == 1 else -1
    m = re.search(r'^chown -R "\$U:\$U" "\$POOL/media"[^\n]*\n', text, flags=re.M)
    if start < 0 or not m:
        raise GenError(f"{W}: mkdir/chown block not found")
    pool = [f"media/{d}" for d in BASE_MEDIA] + [d for a, _ in sel for d in a["pool_dirs"]]
    home = [d for a, _ in sel for d in a["home_dirs"]]
    pool, home = list(dict.fromkeys(pool)), list(dict.fromkeys(home))
    block = 'mkdir -p "$H/app/repos"\n'
    block += "".join(f'mkdir -p "$POOL/{d}"\n' for d in pool)
    block += "".join(f'mkdir -p "$H/{d}"\n' for d in home)
    tops = list(dict.fromkeys(d.split("/")[0] for d in pool))
    block += 'chown -R "$U:$U" ' + " ".join(f'"$POOL/{t}"' for t in tops) + "\n"
    text = text[:start] + block + text[m.end():]

    # stack files copied to ~/app/repos (+ config files copied once)
    m = re.search(r'^cp "\$STAGE/docker-compose\.yml"[^\n]*\n(cp (-rT )?"\$STAGE/[^\n]*\n)*', text, flags=re.M)
    if not m:
        raise GenError(f"{W}: cp \"$STAGE/...\" block not found")
    files = ["docker-compose.yml", "wizardall2.sh"] + [f for a, _ in sel for f in a["stage_files"]]
    block = "".join(f'cp "$STAGE/{f}"{" " * (1 + max(0, 18 - len(f)))}"$H/app/repos/"\n' for f in files)
    # folders: -T copies over an existing folder on a retry (instead of nesting it)
    block += "".join(f'cp -rT "$STAGE/{d}" "$H/app/repos/{d}"\n' for a, _ in sel for d in a["stage_dirs"])
    for a, _ in sel:
        for cf in a["config_files"]:
            block += f'[ -e "$H/{cf["dest"]}" ] || cp "$STAGE/{cf["name"]}" "$H/{cf["dest"]}"\n'
    text = text[:m.start()] + block + text[m.end():]

    # credential files for the wizard POSTs
    m = re.search(r"^# REQ\.3: generate the credential json files.*?^PY\n", text, flags=re.M | re.S)
    if not m:
        raise GenError(f"{W}: credentials block (REQ.3 ... PY) not found")
    creds = [a["credentials"] for a, _ in sel if a.get("credentials")]
    block = ""
    if creds:
        block = ("# REQ.3: generate the credential json files for the wizardall2.sh POSTs\n"
                 "# from the credentials collected by the webapp at 1st boot\n"
                 "python3 - \"$H\" <<'PY'\nimport json, sys\nh = sys.argv[1]\n"
                 "d = json.load(open('/var/lib/setup-data.json'))\n")
        for c in creds:
            fields = ", ".join(f'"{k}": d["{v}"]' for k, v in c["fields"].items())
            block += f'json.dump({{{fields}}},\n          open(h + "/app/repos/{c["file"]}", "w"))\n'
        block += "PY\n"
    end = m.end() + (1 if not creds and text[m.end():m.end() + 1] == "\n" else 0)
    text = text[:m.start()] + block + text[end:]

    # .env placeholders
    env_line = 'sed "s|__HOME__|$H|g" "$STAGE/env.template" > "$H/app/repos/.env"\n'
    if any(re.search(r"=(__RANDOM__|__USER__|__PASSWORD__|__EMAIL__)$", render(a["sections"]["env"], p, a, c, "env"), re.M)
           for a, c in sel):
        text = replace_once(text, env_line, env_line + SECRETS_PY, W)

    # Samba
    host, label = p["system"]["hostname"], p["system"]["server_label"]
    if p["services"]["samba"]:
        text = replace_once(text, "(\\\\egocenter.local\\media)", f"(\\\\{host}.local\\media)", W)
        text = replace_once(text, "   server string = EgoCenter\n", f"   server string = {label}\n", W)
        text = replace_once(text, '<name replace-wildcards="yes">EgoCenter</name>',
                            f'<name replace-wildcards="yes">{label}</name>', W)
    else:
        m = re.search(r"^# --- SMB: .*?^systemctl restart wsdd2 \|\| true\n", text, flags=re.M | re.S)
        if not m:
            raise GenError(f"{W}: Samba block not found")
        text = text[:m.start()] + "# --- SMB: disabled in this image (EgoBuilder) ---\n" + text[m.end():]
    return text


# --------------------------------------------------------------------------
# System files
# --------------------------------------------------------------------------
def hash_password(pw):
    try:
        r = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=pw + "\n",
                           capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        raise GenError(f"openssl passwd -6 not available: {e}")
    return r.stdout.strip()


def gen_preseed(text, p, root_hash):
    W = "preseed.cfg"
    s, loc = p["system"], p["system"]["locale"] + ".UTF-8"
    kb = s["keyboard"]
    text = sub_once(text, r"^d-i debian-installer/locale string .*$", f"d-i debian-installer/locale string {loc}", W)
    text = sub_once(text, r"^d-i localechooser/supported-locales multiselect .*$",
                    f"d-i localechooser/supported-locales multiselect {loc}", W)
    text = sub_once(text, r"^d-i keyboard-configuration/xkb-keymap select .*$",
                    f"d-i keyboard-configuration/xkb-keymap select {kb}", W)
    text = sub_once(text, r"^d-i keyboard-configuration/layoutcode string .*$",
                    f"d-i keyboard-configuration/layoutcode string {kb}", W)
    text = sub_once(text, r"^d-i console-setup/layoutcode string .*$", f"d-i console-setup/layoutcode string {kb}", W)
    text = sub_once(text, r"^d-i netcfg/get_hostname string .*$", f"d-i netcfg/get_hostname string {s['hostname']}", W)
    text = sub_once(text, r"^d-i netcfg/get_domain string .*$", f"d-i netcfg/get_domain string {s['domain']}", W)
    text = sub_once(text, r"^d-i time/zone string .*$", f"d-i time/zone string {s['timezone']}", W)
    text = sub_once(text, r"^d-i partman-auto/choose_recipe select .*$",
                    f"d-i partman-auto/choose_recipe select {p['install']['partitioning']}", W)
    # root: hash only, never the clear password
    text = sub_once(text, r"^d-i passwd/root-password password .*\nd-i passwd/root-password-again password .*$",
                    f"d-i passwd/root-password-crypted password {root_hash}", W)
    if p["install"]["after_install"] == "reboot":
        text = sub_once(text, r"^d-i debian-installer/exit/poweroff boolean true$",
                        "d-i debian-installer/exit/poweroff boolean false", W)
        text = text.replace("# Shutdown after install (remove USB before the 1st boot)",
                            "# Reboot after install (EgoBuilder): the USB must not boot first")
    sv = p["services"]
    # Debian 13: /etc/locale.conf is the real file; tmpfiles (debian.conf, L+) replaces
    # /etc/default/locale with a link to it at every boot. d-i in "live" mode leaves it
    # at C.UTF-8 (seen in a VM install of an EgoBuilder ISO): set it explicitly
    late = [f"in-target sh -c 'echo LANG={loc} > /etc/locale.conf'"]
    if not sv["cockpit"]:
        text = replace_once(text, " docker cockpit.socket; \\", " docker; \\", W)
    if not sv["samba"]:
        text = replace_once(text, "enable avahi-daemon wsdd2 docker", "enable avahi-daemon docker", W)
        late.append("in-target sh -c 'systemctl mask smbd nmbd wsdd2 || true'")
    if not sv["ssh"]:
        text = replace_once(text, "in-target sh -c 'systemctl enable ssh || systemctl enable ssh.socket || true'; \\",
                            "in-target sh -c 'systemctl disable ssh ssh.socket || true'; \\", W)
    if p["accounts"]["lock_root"]:
        late.append("in-target passwd -l root")
    if late:
        text = replace_once(text, "    ln -sf /run/systemd/resolve/stub-resolv.conf /target/etc/resolv.conf\n",
                            "    ln -sf /run/systemd/resolve/stub-resolv.conf /target/etc/resolv.conf; \\\n"
                            + "; \\\n".join("    " + l for l in late) + "\n", W)
    return text


def gen_setup_webapp(text, p):
    W = "rootfs/usr/local/bin/setup-webapp.sh"
    st = p["setup"]
    text = replace_once(text, "ssid=SetupBox\n", f"ssid={st['ssid']}\n", W)
    text = replace_once(text, "wpa_passphrase=setup1234\n", f"wpa_passphrase={st['wifi_password']}\n", W)
    text = replace_once(text, "country_code=IT\n", f"country_code={st['wifi_country']}\n", W)
    text = replace_once(text, "address=/setupbox.local/", f"address=/{st['mdns_name']}.local/", W)
    text = replace_once(text, '<name replace-wildcards="yes">SetupBox</name>', f'<name replace-wildcards="yes">{st["ssid"]}</name>', W)
    text = text.replace("(egocenter.local)", f"({p['system']['hostname']}.local)")
    if p["accounts"]["user_mode"] == "preset":
        # credentials shipped in the image: skip access point, portal and webapp
        text = replace_once(text, "# --- WiFi AP (only if hardware exists) ---\n",
                            "# EgoBuilder: user preset in the image -> no access point, no setup page\n"
                            "if [ ! -f /var/lib/setup-data.json ]; then\n\n"
                            "# --- WiFi AP (only if hardware exists) ---\n", W)
        text = replace_once(text, "kill $WEBAPP_PID 2>/dev/null || true\n",
                            "kill $WEBAPP_PID 2>/dev/null || true\nfi\n", W)
    return text


def read_repo(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


def stage_files(p, catalog=None):
    """{relpath: content or None (delete)} + {relpath: mode} for the new files."""
    catalog = catalog or load_catalog()
    sel = selected_apps(p, catalog)
    rd = read_repo
    files, modes = {}, {}
    s, sv = p["system"], p["services"]
    root_hash = hash_password(p["accounts"]["root_password"])

    # versions of the stock apps (build_var) -> build/common.sh
    common = rd("build/common.sh")
    for a, cfg in sel:
        if a.get("build_var"):
            common = sub_once(common, rf'^{a["build_var"]}="[^"]*"', f'{a["build_var"]}="{cfg["version"]}"', "build/common.sh")
    files["build/common.sh"] = common

    # stack
    files["stack/docker-compose.yml"] = gen_compose(p, sel)
    files["stack/env.template.in"] = gen_env(p, sel, rd("stack/env.template.in"))
    files["stack/wizardall2.sh"] = gen_wizard(p, sel)
    jf = next((c for a, c in sel if a["id"] == "jellyfin"), None)
    if jf:
        files["stack/jellsrv.json"] = gen_jellsrv(p, jf)
    else:
        files["stack/jellsrv.json"] = files["stack/jellremot.json"] = None
    # staged folders of the apps that are not selected: left out of the image
    used = {d for a, _ in sel for d in a["stage_dirs"]}
    for a in catalog.values():
        for d in a["stage_dirs"]:
            if d not in used:
                base = os.path.join(REPO, "stack", d)
                for root, _, names in os.walk(base):
                    for n in names:
                        files["stack/" + os.path.relpath(os.path.join(root, n), os.path.join(REPO, "stack"))] = None
    for a, _ in sel:
        for cf in a["config_files"]:
            if cf.get("generate") == "homepage_services":
                files["stack/" + cf["name"]] = gen_homepage_services(p, sel)
    files["rootfs/usr/local/bin/dirtree.sh"] = gen_dirtree(rd("rootfs/usr/local/bin/dirtree.sh"), p, sel)

    # amd64 installer + build checks of stock apps that are not selected
    files["build/amd64/live-build/includes.installer/preseed.cfg"] = gen_preseed(
        rd("build/amd64/live-build/includes.installer/preseed.cfg"), p, root_hash)
    extra = list(p["packages"]["extra"]) + (["unattended-upgrades"] if sv["auto_updates"] else [])
    pl = rd("build/amd64/live-build/package-lists/egos.list.chroot")
    if extra:
        pl += "# EgoBuilder: extra packages\n" + "".join(x + "\n" for x in extra)
    files["build/amd64/live-build/package-lists/egos.list.chroot"] = pl
    ids = {a["id"] for a, _ in sel}
    script = "build/amd64/EgOS4-amd64.sh"
    t = rd(script)
    if "immich" not in ids:
        t = drop_check(t, 'grep -q "^IMMICH_VERSION=$IMMICH_VERSION$"', script)
    if "jellyfin" not in ids:
        t = drop_check(t, "grep -q '^JELLYFIN_MEDIA_LOCATION=/srv/egos/pool/media$'", script)
    files[script] = t

    # first boot
    files["rootfs/usr/local/bin/setup-webapp.sh"] = gen_setup_webapp(rd("rootfs/usr/local/bin/setup-webapp.sh"), p)
    files["webapp/webapp.py"] = replace_once(
        rd("webapp/webapp.py"),
        "Connect to <strong>SetupBox</strong> WiFi or visit <strong>http://setupbox.local</strong>",
        f"Connect to <strong>{p['setup']['ssid']}</strong> WiFi or visit <strong>http://{p['setup']['mdns_name']}.local</strong>",
        "webapp/webapp.py")
    files["rootfs/etc/avahi/avahi-daemon.conf"] = sub_once(
        rd("rootfs/etc/avahi/avahi-daemon.conf"), r"^host-name=setupbox$", f"host-name={p['setup']['mdns_name']}",
        "avahi-daemon.conf")
    issue = rd("rootfs/etc/issue.in").replace("egocenter.local", f"{s['hostname']}.local")
    eh = next((c for a, c in sel if a["id"] == "egos-home"), None)
    if eh:
        issue = sub_once(issue, r"(^  Home page: .*:)8000$", rf"\g<1>{int(eh['ports'].get('web', 8000))}", "issue.in")
    else:
        issue = sub_once(issue, r"^  Home page: [^\n]*\n", "", "issue.in")
    files["rootfs/etc/issue.in"] = issue
    files["rootfs/usr/share/cockpit/egos-storage/egos-storage.js"] = replace_once(
        rd("rootfs/usr/share/cockpit/egos-storage/egos-storage.js"),
        '"Samba share \\\\\\\\egocenter\\\\media"', f'"Samba share \\\\\\\\{s["hostname"]}\\\\media"',
        "egos-storage.js")
    files["rootfs/etc/egos-release.in"] = rd("rootfs/etc/egos-release.in") + \
        f"EGOS_PROFILE={p['profile_name']}\nEGOS_APPS={','.join(a['id'] for a, _ in sel)}\n"

    if p["accounts"]["user_mode"] == "preset":
        acc = p["accounts"]
        files["rootfs/var/lib/setup-data.json"] = json.dumps(
            {"email": acc["email"], "user": acc["user"], "password": acc["password"]})
        modes["rootfs/var/lib/setup-data.json"] = 0o600
    if sv["ssh_keys"].strip():
        keys = "".join(l.strip() + "\n" for l in sv["ssh_keys"].splitlines() if l.strip())
        files["rootfs/etc/skel/.ssh/authorized_keys"] = keys
        modes["rootfs/etc/skel/.ssh/authorized_keys"] = 0o600
        modes["rootfs/etc/skel/.ssh"] = 0o700
    if sv["ssh"] and not sv["ssh_password_auth"]:
        files["rootfs/etc/ssh/sshd_config.d/10-egos.conf"] = \
            "# EgoBuilder: key-only SSH login\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
    if sv["docker_log_limit"]:
        files["rootfs/etc/docker/daemon.json"] = \
            '{\n  "log-driver": "json-file",\n  "log-opts": {"max-size": "10m", "max-file": "3"}\n}\n'
    if sv["auto_updates"]:
        files["rootfs/etc/apt/apt.conf.d/20auto-upgrades"] = \
            'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n'
    return files, modes


# --------------------------------------------------------------------------
# Staging, preview
# --------------------------------------------------------------------------
STAGE_SKIP = {".git", "out", "egobuilder", ".github", ".claude", "compose.yaml", "Dockerfile", ".dockerignore"}


def redacted(p):
    r = copy.deepcopy(p)
    r["accounts"]["root_password"] = "***" if r["accounts"]["root_password"] else ""
    r["accounts"]["password"] = "***" if r["accounts"]["password"] else ""
    return r


def stage(profile, dest, run_check=True):
    catalog = load_catalog()
    v = validate(profile, catalog)
    if v["errors"]:
        raise GenError("invalid profile: " + "; ".join(f"{e['field']}: {e['msg']}" for e in v["errors"]))
    p = normalize(profile, catalog)
    files, modes = stage_files(p, catalog)
    if os.path.exists(dest) and os.listdir(dest):
        raise GenError(f"{dest} is not empty")
    os.makedirs(dest, exist_ok=True)
    for name in sorted(os.listdir(REPO)):
        if name in STAGE_SKIP:
            continue
        src, dst = os.path.join(REPO, name), os.path.join(dest, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    for rel, content in files.items():
        path = os.path.join(dest, rel)
        if content is None:
            if os.path.exists(path):
                os.remove(path)
            continue
        old_mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, modes.get(rel, old_mode))
    for rel, mode in modes.items():
        os.chmod(os.path.join(dest, rel), mode)
    with open(os.path.join(dest, "egobuilder-profile.json"), "w") as f:
        json.dump(redacted(p), f, indent=2, ensure_ascii=False)
    with open(os.path.join(dest, "EGOBUILDER.md"), "w") as f:
        f.write(build_readme(p, catalog))
    check = None
    if run_check:
        r = subprocess.run(["bash", "tools/check.sh"], cwd=dest, capture_output=True, text=True)
        check = {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()}
    return {"warnings": v["warnings"], "check": check, "files": sorted(files)}


def build_readme(p, catalog):
    sel = selected_apps(p, catalog)
    host = p["system"]["hostname"]
    lines = [f"# EgOS — profile `{p['profile_name']}` (EgoBuilder)", "",
             "The EgOS repo tree with the profile choices already applied. It builds like the repo:", "",
             "```sh", "make check",
             "make iso-amd64",
             "```", "", "## After installation", "", "| App | Address |", "|---|---|"]
    for a, cfg in sel:
        url = app_url(p, a, cfg)
        if url:
            lines.append(f"| {a['name']} | {url} |")
    if p["services"]["cockpit"]:
        lines.append(f"| Cockpit | https://{host}.local:9090 |")
    if p["services"]["samba"]:
        lines.append(f"| Shared folder | `\\\\{host}\\media` · `smb://{host}.local/media` |")
    lines += ["", "Passwords left empty in the profile are generated on the machine at the 2nd boot",
              "and kept in `/var/lib/egos/stack-secrets.json` (root only).", ""]
    return "\n".join(lines)


def preview(profile):
    """Generated files with a unified diff against the repo version."""
    catalog = load_catalog()
    p = normalize(profile, catalog)
    if len(p["accounts"]["root_password"]) < 1:
        p["accounts"]["root_password"] = "x"           # preview only: the hash is not shown anyway
    files, _ = stage_files(p, catalog)
    out = []
    for rel in sorted(files):
        new = files[rel]
        path = os.path.join(REPO, rel)
        old = read_repo(rel) if os.path.exists(path) else None
        if rel.endswith("setup-data.json") and new:
            new = re.sub(r'"password": ".*?"', '"password": "***"', new)
        if rel.endswith("preseed.cfg") and new:
            new = re.sub(r"(root-password-crypted password ).*", r"\1<hash SHA-512>", new)
        diff = "".join(difflib.unified_diff((old or "").splitlines(True), (new or "").splitlines(True),
                                            f"a/{rel}", f"b/{rel}", n=2))
        out.append({"path": rel, "status": "deleted" if new is None else ("new" if old is None else
                    ("changed" if diff else "same")), "content": new, "diff": diff})
    return out


def main(argv):
    if argv[1:2] == ["--default"]:
        json.dump(default_profile(), sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    with open(argv[1]) as f:
        profile = json.load(f)
    try:
        r = stage(profile, argv[2])
    except GenError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for w in r["warnings"]:
        print(f"warning: {w['field']}: {w['msg']}")
    print(r["check"]["output"])
    print(f"tree ready: {argv[2]}")
    return 0 if r["check"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
