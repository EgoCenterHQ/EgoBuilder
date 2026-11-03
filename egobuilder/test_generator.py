"""python3 -m unittest discover -s egobuilder   (from the repo root)"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generator as g  # noqa: E402

REPO = g.REPO


def stock(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


def profile(**over):
    """Default profile = what EgOS ships today (Immich + Jellyfin + EgosHome), stock DB password."""
    p = g.default_profile()
    p["accounts"]["root_password"] = "CHANGE_ME"
    p["apps"]["immich"]["secrets"]["db_password"] = "password"
    for path, v in over.items():
        d = p
        keys = path.split("__")
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = v
    return p


class StockIdentity(unittest.TestCase):
    """The default profile must reproduce the stock stack: the catalog fragments are the stock files."""

    def setUp(self):
        self.files, _ = g.stage_files(g.normalize(profile()))

    def test_compose(self):
        self.assertEqual(self.files["stack/docker-compose.yml"], stock("stack/docker-compose.yml"))

    def test_env_template(self):
        self.assertEqual(self.files["stack/env.template.in"], stock("stack/env.template.in"))

    def test_wizard(self):
        self.assertEqual(self.files["stack/wizardall2.sh"], stock("stack/wizardall2.sh"))

    def test_jellsrv(self):
        self.assertEqual(self.files["stack/jellsrv.json"], stock("stack/jellsrv.json"))

    def test_dirtree_same_lines(self):
        new, old = self.files["rootfs/usr/local/bin/dirtree.sh"], stock("rootfs/usr/local/bin/dirtree.sh")
        self.assertEqual(sorted(new.splitlines()), sorted(old.splitlines()))
        self.assertNotIn("stack-secrets", new)

    def test_unchanged_system_files(self):
        for rel in ("build/common.sh", "rootfs/usr/local/bin/setup-webapp.sh", "webapp/webapp.py",
                    "rootfs/etc/avahi/avahi-daemon.conf", "rootfs/etc/issue.in",
                    "build/amd64/EgOS4-amd64.sh", "build/amd64/live-build/package-lists/egos.list.chroot"):
            self.assertEqual(self.files[rel], stock(rel), rel)

    def test_preseed_only_root_hash(self):
        new = self.files["build/amd64/live-build/includes.installer/preseed.cfg"]
        self.assertNotIn("CHANGE_ME", new)
        self.assertRegex(new, r"d-i passwd/root-password-crypted password \$6\$")
        self.assertIn("in-target sh -c 'echo LANG=it_IT.UTF-8 > /etc/locale.conf'", new)
        skip = lambda l: "root-password" in l or "/etc/locale.conf" in l or "stub-resolv.conf" in l
        old = [l for l in stock("build/amd64/live-build/includes.installer/preseed.cfg").splitlines() if not skip(l)]
        self.assertEqual([l for l in new.splitlines() if not skip(l)], old)


class Customised(unittest.TestCase):
    def test_no_jellyfin(self):
        p = profile()
        p["apps"]["jellyfin"]["enabled"] = False
        files, _ = g.stage_files(g.normalize(p))
        self.assertIsNone(files["stack/jellsrv.json"])
        self.assertNotIn("8096", files["stack/wizardall2.sh"])
        self.assertNotIn("jelluser", files["rootfs/usr/local/bin/dirtree.sh"])
        self.assertNotIn("JELLYFIN_MEDIA_LOCATION", files["build/amd64/EgOS4-amd64.sh"])
        self.assertIn("immichset.json", files["rootfs/usr/local/bin/dirtree.sh"])

    def test_egos_home_recommended_and_removable(self):
        self.assertTrue(g.default_profile()["apps"]["egos-home"]["enabled"])
        p = profile()
        p["apps"]["egos-home"]["enabled"] = False
        files, _ = g.stage_files(g.normalize(p))
        self.assertNotIn("egos-home", files["stack/docker-compose.yml"])
        self.assertNotIn("docker-proxy", files["stack/docker-compose.yml"])
        self.assertNotIn('"$STAGE/egos-home"', files["rootfs/usr/local/bin/dirtree.sh"])
        self.assertNotIn("Home page", files["rootfs/etc/issue.in"])
        self.assertIsNone(files["stack/egos-home/home.py"])
        self.assertIsNone(files["stack/egos-home/icons/jellyfin.svg"])

    def test_egos_home_ports(self):
        p = profile()
        p["apps"]["egos-home"]["ports"] = {"web": 8100, "proxy": 23800}
        self.assertEqual(g.validate(p)["errors"], [])
        files, _ = g.stage_files(g.normalize(p))
        c = files["stack/docker-compose.yml"]
        self.assertIn("- PORT=8100", c)
        self.assertIn("'127.0.0.1:23800:2375'", c)
        self.assertIn("- DOCKER_API=http://127.0.0.1:23800", c)
        self.assertIn("  Home page:     http://egocenter.local:8100\n", files["rootfs/etc/issue.in"])
        self.assertNotIn("stack/egos-home/home.py", files)

    def test_language_and_names(self):
        p = profile(system__locale="de_DE", system__keyboard="de", system__timezone="Europe/Berlin",
                    system__hostname="heimserver")
        files, _ = g.stage_files(g.normalize(p))
        ps = files["build/amd64/live-build/includes.installer/preseed.cfg"]
        for line in ("d-i debian-installer/locale string de_DE.UTF-8", "xkb-keymap select de",
                     "get_hostname string heimserver", "time/zone string Europe/Berlin"):
            self.assertIn(line, ps)
        self.assertIn('"UICulture":"de"', files["stack/jellsrv.json"])
        self.assertIn("TZ=Europe/Berlin", files["stack/env.template.in"])
        self.assertIn("in-target sh -c 'echo LANG=de_DE.UTF-8 > /etc/locale.conf'", ps)
        self.assertIn("heimserver.local", files["rootfs/etc/issue.in"])

    def test_preset_user_and_random_secrets(self):
        p = profile(accounts__user_mode="preset", accounts__user="anna", accounts__email="a@b.it",
                    accounts__password="segreto123")
        p["apps"]["immich"]["secrets"]["db_password"] = ""
        p["apps"]["nextcloud"]["enabled"] = True
        files, modes = g.stage_files(g.normalize(p))
        self.assertEqual(json.loads(files["rootfs/var/lib/setup-data.json"])["user"], "anna")
        self.assertEqual(modes["rootfs/var/lib/setup-data.json"], 0o600)
        sw = files["rootfs/usr/local/bin/setup-webapp.sh"]
        self.assertIn("if [ ! -f /var/lib/setup-data.json ]; then", sw)
        r = subprocess.run(["bash", "-n"], input=sw, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("DB_PASSWORD=__RANDOM__", files["stack/env.template.in"])
        self.assertIn("NEXTCLOUD_ADMIN_PASSWORD=__PASSWORD__", files["stack/env.template.in"])
        self.assertIn("stack-secrets.json", files["rootfs/usr/local/bin/dirtree.sh"])

    def test_secrets_script_runs(self):
        """The .env placeholder filler embedded in dirtree.sh, run on a sample .env."""
        src = g.SECRETS_PY.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as t:
            env, data, store = (os.path.join(t, n) for n in (".env", "setup.json", "s.json"))
            open(env, "w").write("A=__RANDOM__\nB=__PASSWORD__\nC=x\n")
            json.dump({"user": "u", "password": "p w#1", "email": "e"}, open(data, "w"))
            code = src.replace("/var/lib/setup-data.json", data).replace("/var/lib/egos/stack-secrets.json", store)
            subprocess.run([sys.executable, "-c", code, env], check=True)
            first = open(env).read()
            self.assertRegex(first, r"A=[0-9a-f]{32}\nB='p w#1'\nC=x\n")
            open(env, "w").write("A=__RANDOM__\n")
            subprocess.run([sys.executable, "-c", code, env], check=True)
            self.assertEqual(open(env).read().split("\n")[0], first.split("\n")[0])   # stable across retries

    def test_samba_ssh_cockpit_off(self):
        p = profile(services__samba=False, services__cockpit=False, services__ssh=False, accounts__lock_root=True)
        files, _ = g.stage_files(g.normalize(p))
        dt = files["rootfs/usr/local/bin/dirtree.sh"]
        self.assertNotIn("smbpasswd", dt)
        self.assertEqual(subprocess.run(["bash", "-n"], input=dt, text=True).returncode, 0)
        ps = files["build/amd64/live-build/includes.installer/preseed.cfg"]
        self.assertNotIn("cockpit.socket", ps)
        self.assertIn("systemctl mask smbd nmbd wsdd2", ps)
        self.assertIn("in-target passwd -l root", ps)
        self.assertIn("systemctl disable ssh ssh.socket", ps)

    def test_all_apps_render(self):
        p = profile()
        for a in p["apps"].values():
            a["enabled"] = True
        v = g.validate(p)
        self.assertEqual(v["errors"], [])
        files, _ = g.stage_files(g.normalize(p))
        self.assertNotIn("{{", files["stack/docker-compose.yml"] + files["stack/env.template.in"])
        self.assertIn("- Navidrome:", files["stack/homepage-services.yaml"])
        self.assertIn('[ -e "$H/app/homepage/config/services.yaml" ]', files["rootfs/usr/local/bin/dirtree.sh"])
        for t in ("stack/wizardall2.sh", "rootfs/usr/local/bin/dirtree.sh"):
            self.assertEqual(subprocess.run(["bash", "-n"], input=files[t], text=True).returncode, 0, t)


class Validation(unittest.TestCase):
    def fields(self, p):
        return {e["field"] for e in g.validate(p)["errors"]}

    def test_default_needs_root_password(self):
        self.assertIn("accounts.root_password", self.fields(g.default_profile()))
        self.assertEqual(self.fields(profile()), set())

    def test_port_conflicts(self):
        p = profile()
        p["apps"]["navidrome"]["enabled"] = True
        p["apps"]["navidrome"]["ports"]["web"] = 2283
        self.assertIn("apps.navidrome.ports.web", self.fields(p))
        p["apps"]["navidrome"]["ports"]["web"] = 9090
        self.assertIn("apps.navidrome.ports.web", self.fields(p))

    def test_bad_values(self):
        p = profile(system__hostname="Bad_Host", system__domain="local", accounts__user_mode="preset",
                    accounts__user="root", accounts__email="x", accounts__password="short")
        self.assertTrue({"system.hostname", "system.domain", "accounts.user", "accounts.email",
                         "accounts.password"} <= self.fields(p))

    def test_custom_app(self):
        p = profile()
        p["custom_apps"] = [{"id": "whoami", "name": "Whoami",
                             "compose": "whoami:\n  image: traefik/whoami\n  ports:\n    - '8099:80'\n",
                             "env": "WHOAMI_X=1"}]
        self.assertEqual(self.fields(p), set())
        files, _ = g.stage_files(g.normalize(p))
        self.assertIn("  whoami:\n    image: traefik/whoami", files["stack/docker-compose.yml"])
        p["custom_apps"][0]["compose"] = "whoami:\n  image: x\n  ports:\n    - '2283:80'\n"
        self.assertTrue(any(f.startswith("custom.whoami") for f in self.fields(p)))


class Stage(unittest.TestCase):
    def test_stage_and_check(self):
        p = profile()
        p["apps"]["paperless"]["enabled"] = True
        with tempfile.TemporaryDirectory() as t:
            r = g.stage(p, os.path.join(t, "tree"))
            self.assertTrue(r["check"]["ok"], r["check"]["output"])
            prof = json.load(open(os.path.join(t, "tree", "egobuilder-profile.json")))
            self.assertEqual(prof["accounts"]["root_password"], "***")
            self.assertFalse(os.path.exists(os.path.join(t, "tree", "egobuilder")))


if __name__ == "__main__":
    unittest.main()
