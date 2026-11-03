#!/usr/bin/env python3
"""EgoBuilder web app: build a customised EgOS image from the browser.

    python3 egobuilder/server.py [--port 8765] [--host 127.0.0.1]
    make builder

Only the Python standard library. Work goes to out/egobuilder/<job>/:
    tree/      the repo tree with the profile applied (generator.py)
    iso/       the ISO and nothing else (the build runs in a throwaway container)
    build.log  job.json

The ISO build needs a privileged container (live-build mounts /proc, creates
device nodes): the server must run as root or have passwordless sudo. Without
that, the page shows the exact command to run by hand.
"""
import argparse
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import generator as g

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
OUT = os.environ.get("EGOBUILDER_DATA") or os.path.join(g.REPO, "out", "egobuilder")
JOB_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")
CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
      ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

LOCK = threading.Lock()
PROCS = {}                      # job id -> Popen of a running build


# --------------------------------------------------------------------------
# Build engine
# --------------------------------------------------------------------------
def has_cap_sys_admin():
    try:
        with open("/proc/self/status") as f:
            eff = next(l for l in f if l.startswith("CapEff:")).split()[1]
        return bool(int(eff, 16) & (1 << 21))
    except (OSError, StopIteration, ValueError):
        return False


def detect_engine():
    root = os.geteuid() == 0
    if os.environ.get("EGOBUILDER_LOCAL_BUILD") == "1":
        # inside the EgoBuilder container: live-build runs right here, no nested
        # container. Needs `docker run --privileged` (mounts /proc, device nodes)
        priv = root and has_cap_sys_admin()
        return {"engine": "local", "root": root, "sudo": False, "privileged": priv,
                "can_build": bool(priv and shutil.which("lb")), "prefix": []}
    sudo = [] if root else ["sudo", "-n"]
    sudo_ok = root or (shutil.which("sudo") is not None and
                       subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0)
    engine = None
    for e in (os.environ.get("ENGINE"), "docker", "podman"):
        if not e or not shutil.which(e):
            continue
        if e == "docker" and sudo_ok and subprocess.run(sudo + ["docker", "info"], capture_output=True).returncode != 0:
            continue
        engine = e
        break
    if engine is None:
        engine = "podman" if shutil.which("podman") else ("docker" if shutil.which("docker") else None)
    return {"engine": engine, "root": root, "sudo": sudo_ok, "privileged": None, "can_build": bool(engine and sudo_ok),
            "prefix": sudo if sudo_ok else ["sudo"]}


ENGINE = None

BUILD_SH = r"""set -e
export DEBIAN_FRONTEND=noninteractive
echo "== builder: installing live-build"
apt-get update -qq
apt-get install -y -qq live-build curl ca-certificates gnupg >/dev/null
mkdir -p /build && cd /build
rc=0
bash /src/build/amd64/EgOS4-amd64.sh || rc=$?
cp egos-*-amd64.iso /out/ 2>/dev/null || true
chown {uid}:{gid} /out/*.iso 2>/dev/null || true
echo "exit=$rc"
exit $rc
"""


LOCAL_SH = r"""set -e
WORK={work}
mkdir -p "$WORK" && cd "$WORK"
rc=0
bash {tree}/build/amd64/EgOS4-amd64.sh || rc=$?
mv egos-*-amd64.iso {iso}/ 2>/dev/null || true
cd /
# never rm -rf a chroot that still has /proc or /dev mounted in it
if grep -q " $WORK/" /proc/mounts; then echo "mounts left under $WORK: work dir kept"; else rm -rf "$WORK"; fi
echo "exit=$rc"
exit $rc
"""


def build_command(job, manual=False):
    tree, iso = os.path.join(job["dir"], "tree"), os.path.join(job["dir"], "iso")
    if ENGINE["engine"] == "local":
        q = shlex.quote
        return ["bash", "-c", LOCAL_SH.format(work=q(os.path.join(job["dir"], "work")), tree=q(tree), iso=q(iso))]
    prefix = ["sudo"] if manual and not ENGINE["root"] else ENGINE["prefix"]
    return prefix + [ENGINE["engine"] or "podman", "run", "--rm", "--privileged", "--network=host",
                     "--name", f"egobuilder-{job['id']}",
                     "-v", f"{tree}:/src:ro,Z", "-v", f"{iso}:/out:Z",
                     "docker.io/library/debian:trixie", "bash", "-c",
                     BUILD_SH.format(uid=os.getuid(), gid=os.getgid())]


def shell_command(job):
    return " ".join(shlex.quote(c) for c in build_command(job, manual=True))


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
def job_path(jid, *rest):
    if not JOB_RE.match(jid):
        raise KeyError(jid)
    return os.path.join(OUT, jid, *rest)


def load_job(jid):
    with open(job_path(jid, "job.json")) as f:
        job = json.load(f)
    if job["status"] == "building" and jid not in PROCS:
        job["status"] = "interrupted"            # server restarted during a build
    isos = sorted(os.listdir(job_path(jid, "iso"))) if os.path.isdir(job_path(jid, "iso")) else []
    job["isos"] = [i for i in isos if i.endswith(".iso")]
    return job


def save_job(job):
    data = {k: v for k, v in job.items() if k != "isos"}
    with open(job_path(job["id"], "job.json"), "w") as f:
        json.dump(data, f, indent=2)


def list_jobs():
    if not os.path.isdir(OUT):
        return []
    out = []
    for jid in sorted(os.listdir(OUT), reverse=True):
        try:
            j = load_job(jid)
        except (KeyError, OSError, ValueError):
            continue
        out.append(public_job(j))
    return out


def public_job(j):
    return {k: j.get(k) for k in ("id", "profile_name", "status", "created", "finished",
                                   "isos", "check", "warnings", "apps")} | {"command": shell_command(j)}


def create_job(profile):
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    d = job_path(jid)
    os.makedirs(d, mode=0o700)
    p = g.normalize(profile)
    job = {"id": jid, "dir": d, "profile_name": p["profile_name"], "status": "staging",
           "created": time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None,
           "apps": [a["id"] for a, _ in g.selected_apps(p, g.load_catalog())]}
    try:
        r = g.stage(profile, os.path.join(d, "tree"))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    os.makedirs(os.path.join(d, "iso"))
    job.update(status="staged" if r["check"]["ok"] else "check-failed", check=r["check"], warnings=r["warnings"])
    save_job(job)
    return load_job(jid)


def start_build(jid):
    with LOCK:
        job = load_job(jid)
        if jid in PROCS:
            raise ValueError("a build is already running")
        if job["status"] == "check-failed":
            raise ValueError("tools/check.sh failed on this tree: no build")
        if not ENGINE["can_build"]:
            raise ValueError("start the container with --privileged" if ENGINE["engine"] == "local"
                             else "needs root or passwordless sudo and docker/podman: use the command shown")
        if job["isos"]:
            raise ValueError("this job already has an ISO")
        if any(j in PROCS for j in os.listdir(OUT)):
            raise ValueError("another build is running: one at a time")
        log = open(job_path(jid, "build.log"), "w")
        log.write("$ " + shell_command(job) + "\n\n")
        log.flush()
        proc = subprocess.Popen(build_command(job), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        PROCS[jid] = proc
        job.update(status="building", started=time.time())
        save_job(job)

    def wait():
        rc = proc.wait()
        log.close()
        with LOCK:
            PROCS.pop(jid, None)
            j = load_job(jid)
            j.update(status="done" if rc == 0 and j["isos"] else ("cancelled" if j.get("cancel") else "failed"),
                     finished=time.strftime("%Y-%m-%d %H:%M:%S"), rc=rc)
            save_job(j)
    threading.Thread(target=wait, daemon=True).start()
    return load_job(jid)


def cancel_build(jid):
    with LOCK:
        job = load_job(jid)
        proc = PROCS.get(jid)
        if not proc:
            raise ValueError("no build running")
        job["cancel"] = True
        save_job(job)
    if ENGINE["engine"] == "local":
        try:
            os.killpg(proc.pid, signal.SIGTERM)       # bash + live-build + its children
        except ProcessLookupError:
            pass
        return
    subprocess.run(ENGINE["prefix"] + [ENGINE["engine"], "kill", f"egobuilder-{jid}"], capture_output=True)
    proc.terminate()


def read_log(jid, offset):
    path = job_path(jid, "build.log")
    if not os.path.exists(path):
        return "", 0
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if offset > size:
            offset = 0
        if size - offset > 512 * 1024:          # first poll of a long log: only the tail
            offset = size - 512 * 1024
        f.seek(offset)
        return f.read().decode("utf-8", "replace"), size


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def meta():
    cat = g.load_catalog()
    apps = []
    for a in cat.values():
        apps.append({k: a.get(k) for k in ("id", "name", "headline", "category", "description", "default_version",
                                           "ports", "secrets", "options", "autosetup", "firstrun", "notes",
                                           "pool_dirs", "home_dirs", "build_var", "recommended")} |
                    {"compose": a["sections"]["compose"], "env": a["sections"]["env"]})
    try:
        tzs = sorted(t for t in zoneinfo.available_timezones() if "/" in t and not t.startswith(("Etc/", "SystemV/")))
    except Exception:
        tzs = []
    with open(os.path.join(g.REPO, "VERSION")) as f:
        version = f.read().strip()
    return {"egos_version": version, "apps": apps, "locales": g.LOCALES, "keyboards": g.KEYBOARDS,
            "partitioning": g.PARTITIONING, "timezones": ["UTC"] + tzs, "base_media": g.BASE_MEDIA,
            "default_profile": g.default_profile(cat),
            "engine": {k: ENGINE[k] for k in ("engine", "root", "sudo", "privileged", "can_build")}}


class Handler(BaseHTTPRequestHandler):
    server_version = "EgoBuilder"

    def log_message(self, fmt, *args):
        if "/api/jobs/" in (args[0] if args else "") and "/log" in args[0]:
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers
    def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, name, ctype):
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1024 * 1024)

    def host_ok(self):
        # DNS rebinding: only answer to the names this server is reached by locally
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def body(self):
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise ValueError("Content-Type application/json required")
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2 * 1024 * 1024:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # -- routes
    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "host not allowed"})
        u = urlparse(self.path)
        path = u.path
        try:
            if path == "/" or path == "/index.html":
                return self.static("index.html")
            if path.startswith("/static/"):
                return self.static(path[len("/static/"):])
            if path == "/api/meta":
                return self.send(200, meta())
            if path == "/api/jobs":
                return self.send(200, {"jobs": list_jobs()})
            m = re.match(r"^/api/jobs/([^/]+)(/log|/tree\.tar\.gz|/iso/([^/]+))?$", path)
            if m:
                jid = m.group(1)
                job = load_job(jid)
                if m.group(2) is None:
                    return self.send(200, public_job(job))
                if m.group(2) == "/log":
                    off = int(parse_qs(u.query).get("offset", ["0"])[0])
                    text, size = read_log(jid, off)
                    return self.send(200, {"text": text, "offset": size, "status": job["status"]})
                if m.group(2) == "/tree.tar.gz":
                    return self.send_tree(job)
                name = m.group(3)
                if name not in job["isos"]:
                    return self.send(404, {"error": "ISO not found"})
                return self.send_file(job_path(jid, "iso", name), name, "application/octet-stream")
            return self.send(404, {"error": "not found"})
        except (KeyError, FileNotFoundError):
            return self.send(404, {"error": "job not found"})

    def do_POST(self):
        if not self.host_ok():
            return self.send(403, {"error": "host not allowed"})
        path = urlparse(self.path).path
        try:
            data = self.body()
            if path == "/api/validate":
                return self.send(200, g.validate(data.get("profile") or {}))
            if path == "/api/preview":
                return self.send(200, {"files": g.preview(data.get("profile") or {})})
            if path == "/api/stage":
                return self.send(200, public_job(create_job(data.get("profile") or {})))
            m = re.match(r"^/api/jobs/([^/]+)/(build|cancel|delete)$", path)
            if m:
                jid, action = m.groups()
                if action == "build":
                    return self.send(200, public_job(start_build(jid)))
                if action == "cancel":
                    cancel_build(jid)
                    return self.send(200, {"ok": True})
                with LOCK:
                    if jid in PROCS:
                        raise ValueError("build running: cancel it first")
                    load_job(jid)
                    shutil.rmtree(job_path(jid))
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "not found"})
        except g.GenError as e:
            return self.send(422, {"error": str(e)})
        except (KeyError, FileNotFoundError):
            return self.send(404, {"error": "job not found"})
        except ValueError as e:
            return self.send(400, {"error": str(e)})

    def static(self, rel):
        path = os.path.realpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
            return self.send(404, {"error": "not found"})
        with open(path, "rb") as f:
            self.send(200, f.read(), CT.get(os.path.splitext(path)[1], "application/octet-stream"))

    def send_tree(self, job):
        tmp = job_path(job["id"], "tree.tar.gz")
        name = f"egos-{job['profile_name']}-{job['id']}"
        with tarfile.open(tmp, "w:gz") as t:
            t.add(job_path(job["id"], "tree"), arcname=name)
        try:
            self.send_file(tmp, name + ".tar.gz", "application/gzip")
        finally:
            os.remove(tmp)


ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"} | \
    {h.strip() for h in os.environ.get("EGOBUILDER_ALLOWED_HOSTS", "").split(",") if h.strip()}


def main():
    global ENGINE
    ap = argparse.ArgumentParser(description="EgoBuilder web app")
    ap.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        if os.environ.get("EGOBUILDER_LOCAL_BUILD") == "1":
            print("container: publish the port locally only (-p 127.0.0.1:8765:8765); "
                  "extra host names in EGOBUILDER_ALLOWED_HOSTS")
        else:
            print(f"WARNING: listening on {args.host}: anyone who can reach the port can start privileged builds.")
        ALLOWED_HOSTS.add(args.host)
    os.makedirs(OUT, exist_ok=True)
    sys.stdout.reconfigure(line_buffering=True)
    ENGINE = detect_engine()
    e = ENGINE
    print(f"EgoBuilder - EgOS repo {g.REPO}")
    print(f"data: {OUT}")
    print(f"engine: {e['engine'] or 'none'}  root: {e['root']}  passwordless sudo: {e['sudo']}  "
          f"build from the browser: {'yes' if e['can_build'] else 'no (the page shows the command)'}")
    print(f"open http://{'localhost' if args.host in ('127.0.0.1', '::1', '0.0.0.0') else args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
