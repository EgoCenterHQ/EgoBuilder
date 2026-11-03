# EgoBuilder

**A web app to build custom EgOS images.** EgOS turns a PC or mini-PC into a home server for photos, movies
and music: you install it from a USB stick, and it sets itself up. EgoBuilder lets you choose the language,
the accounts, the apps (Docker Compose containers) and the services, and builds the ISO for you.

```sh
docker compose up -d --build        # -> http://localhost:8765
```

## What EgOS gives you

- **Phone photos and videos** backed up automatically, like Google Photos but at home (Immich).
- **Movies, series and music** on TV, phone and PC, like Netflix for your own files (Jellyfin).
- **A shared network folder** to copy files from any computer (Samba).
- **More space when you need it**: plug in a USB disk and add it with one click. If a disk is unplugged,
  the server reports it and takes it back by itself when you reconnect it.
- **A control panel in the browser** for the server and its disks (Cockpit).
- **A home page** with a card for every app of the box, found by itself (EgosHome, recommended).
- **Any other app** from the EgoBuilder catalog, or your own compose service.

## Using EgoBuilder

Open http://localhost:8765 and go through five steps:

| Step | What you choose |
|---|---|
| System | language (sets keyboard, time zone and the apps' language too), keyboard, time zone, hostname, domain, network name |
| Accounts | root password (only its SHA-512 hash goes into the image), lock root; the user is either **chosen at first boot** on a setup page, or **preset** in the image; setup Wi-Fi name/password/country |
| Apps | 16 catalog apps — Immich, Jellyfin, EgosHome (recommended, on by default), Navidrome, Audiobookshelf, Kavita, Nextcloud, Paperless-ngx, Syncthing, File Browser, Vaultwarden, Home Assistant, Uptime Kuma, Portainer, Gitea, Homepage — plus custom apps; version, ports, secrets and options for each |
| Services & advanced | SSH (with keys, or keys only), Cockpit, Samba, Docker log limit, automatic security updates, extra Debian packages, disk partitioning, power off or reboot after install |
| Summary & build | errors and warnings, addresses after install, the diff of every generated file, the tree as `.tar.gz`, the ISO build with a live log |

Port conflicts, invalid names and missing passwords are flagged as you type. Database passwords left empty
are generated **on the machine** at the second boot, so they never end up in the ISO.

### Running it

**Docker** (recommended; needs only docker on the host):

```sh
docker compose up -d --build        # start, http://localhost:8765
docker compose logs -f              # server log
docker compose down                 # stop; jobs stay in the egobuilder-data volume
```

The ISO is built inside the container (live-build is in the image), which needs `privileged: true` — already
set in `compose.yaml` — because live-build mounts `/proc` and creates device nodes in its chroot. Without it the
app still works but stops at "Prepare the tree". The container uses the host network (`network_mode: host`,
also for the image build): on some hosts docker's bridge network cannot reach the internet and the build stops
at the first `apt-get update`. The server listens on the host's `127.0.0.1:8765` only.

**Without Docker** (Python 3.11+, standard library only):

```sh
make builder        # = python3 egobuilder/server.py
```

To build the ISO from the browser this way you need docker or podman **and** root or passwordless `sudo`;
otherwise the page shows the command to run by hand.

| Variable | Default | |
|---|---|---|
| `EGOBUILDER_DATA` | `out/egobuilder` (`/data` in the container) | jobs: generated tree, build log, ISO |
| `EGOBUILDER_LOCAL_BUILD` | — (`1` in the container) | build with the local live-build instead of a container |
| `EGOBUILDER_ALLOWED_HOSTS` | — | extra Host names allowed, comma separated |

The server answers only to `localhost`/`127.0.0.1` (DNS rebinding) and accepts POSTs only as JSON (no CSRF).
Whoever can reach it can start privileged builds: do not publish it beyond your machine.

## Installing the ISO

1. Write `egos-<version>-amd64.iso` to a USB stick and boot the PC from it (BIOS or UEFI).
2. The installation runs unattended and the PC powers off. Remove the stick and power on.
   The installer takes the first disk; **with more than one disk it stops and asks**.
3. **First boot** — with a user chosen at first boot: connect to the setup Wi-Fi (default **SetupBox**,
   password `setup1234`) or open **http://setupbox.local** over Ethernet, and choose username, email and password.
   With a preset user this step is skipped. The server restarts.
4. **Second boot** — the server downloads and configures the apps by itself (internet needed, a few minutes).

Then, from any device on the home network (default hostname `egocenter`):

| For | Open |
|---|---|
| Home page with every app (EgosHome) | http://egocenter.local:8000 |
| Photos (Immich) | http://egocenter.local:2283 |
| Movies and music (Jellyfin) | http://egocenter.local:8096 |
| Control panel and disks (Cockpit) | https://egocenter.local:9090 |
| Shared folder | `\\egocenter\media` (Windows) · `smb://egocenter.local/media` (Mac/Linux) |

The Summary step lists the exact addresses for your choices. Everything uses the username and password of
the EgOS user; Immich uses the email instead of the username.

**Status:** EgOS 4.3.0, in development. For the home network only: no firewall yet.

## How it works

The files EgOS adds to Debian live in this repo and are edited in place:

| Path | In the image | |
|---|---|---|
| `rootfs/` | `/` | system files: services, disk pool (`egos-storage`), Cockpit page |
| `stack/` | `/usr/local/share/stack` | Immich + Jellyfin + EgosHome compose file, setup wizard, `.env` template, `egos-home/` (home page) |
| `extras/` | `/usr/local/share/stack/extras` | optional stacks, off by default (`egos-extras`) |
| `webapp/webapp.py` | `/usr/local/bin/webapp.py` | first-boot setup page |
| `build/amd64/live-build/` | live-build `config/` | preseed, package list, hooks |
| `build/common.sh`, `build/amd64/EgOS4-amd64.sh` | — | versions, overlay copy, the ISO build |
| `egobuilder/` | — | the web app |

Files named `foo.in` are templates: every `@NAME@` is replaced at build time (`EGOS_VERSION`, `EGOS_ARCH`,
`BUILD_DATE`, `IMMICH_VERSION`, `JELLYFIN_VERSION`). `VERSION` holds the EgOS version.

**EgoBuilder** (`egobuilder/generator.py`) copies this tree and rewrites the files a profile touches. Every
edit of an existing file has a **checked anchor**: if the expected text is not there exactly once, it stops
instead of producing a half-patched image. Then it runs `tools/check.sh` on the copy, and the build runs on
that copy exactly as `make iso-amd64` runs on the repo.

- **Catalog** — `egobuilder/catalog/<id>.app`: JSON metadata, then the sections `--- compose`, `--- env`,
  `--- wizard-wait|setup|verify`. Placeholders: `{{port.web}}`, `{{version}}`, `{{secret.x}}`, `{{option.x}}`,
  `{{hostname}}`, `{{lang2}}`, `{{lang3}}`. Immich, Jellyfin and EgosHome are the fragments of the stock stack: the
  default profile reproduces `stack/docker-compose.yml`, `env.template.in`, `wizardall2.sh` and `jellsrv.json`
  byte for byte (checked by the tests), so a change to the stock stack must go into `immich.app` /
  `jellyfin.app` / `egos-home.app` too. `"recommended": true` = on by default and marked in the web app;
  `stage_dirs` = folders of `stack/` copied to `~/app/repos` (left out of the image when the app is off).
- **Secrets** — an empty secret becomes `__RANDOM__`, generated at the second boot and kept in
  `/var/lib/egos/stack-secrets.json` (root, 600) so a retry reuses it. `__USER__`, `__PASSWORD__`, `__EMAIL__`
  take the EgOS user's values (Nextcloud and Paperless create their admin this way).
- **Language** — on Debian 13 `/etc/locale.conf` is the real file (tmpfiles replaces `/etc/default/locale`
  with a link to it at every boot): the installer writes it explicitly.

### Building without the web app

```sh
make check          # syntax of every script, overlay dry run, EgoBuilder tests (no root); CI runs it on every push
make iso-amd64      # stock ISO in out/amd64 (sudo podman, privileged debian:trixie container)
```

**WARNING: the stock ISO has a fixed placeholder root password, `CHANGE_ME` (user `root`), written in
`build/amd64/live-build/includes.installer/preseed.cfg`; `YOU` / `CHANGE_ME` are the placeholders for a
default user. Every stock ISO has the same root password: change it before building, or right after installing.**

- Before building: replace `CHANGE_ME` in the two `d-i passwd/root-password` lines of the preseed, or build
  from the web app, which asks for the root password and writes only its SHA-512 hash.
- After installing, logged in as the EgOS user: `sudo passwd root` to set a new root password, or
  `sudo passwd -l root` to lock root (sudo keeps working).

**WARNING: the setup Wi-Fi (default `SetupBox`) has a fixed, public default passphrase, `setup1234`. With a user
chosen at first boot, anyone in Wi-Fi range can join it and create the user in your place until the form is sent.**

- Set your own passphrase in the web app: **Accounts → Setup Wi-Fi** (8–63 characters, or **Generate**); the
  Summary warns while the default is in use. A preset user skips the setup Wi-Fi altogether.
- Do not change `setup1234` in `rootfs/usr/local/bin/setup-webapp.sh`: the web app replaces exactly that line
  (checked anchor in `egobuilder/generator.py`) and would stop. For a stock ISO, do the first boot in a trusted
  place or over Ethernet (**http://setupbox.local**) and send the form right away: after it the access point is
  stopped and its configuration is deleted.

### Tested

- CI: `make check`. `docker compose config` accepts the stack with all 15 apps plus a custom one.
- ISO built both from the host engine and inside the EgoBuilder container, installed in a UEFI/KVM VM:
  unattended install, preset user created without the setup page, second-boot deploy `ALL OK`, Immich and
  Paperless logins with the EgOS user, Homepage links, Navidrome, Cockpit, secrets generated on the machine,
  system language as chosen.

### Limits

- PC (amd64) only.
- Non-stock apps use floating tags (`latest`, `stable`, `lts`) until you pin a version; the page warns about it.
- The Samba share is always named `media` (`egos-storage` relies on it).
- Apps that need DNS on port 53 (Pi-hole, AdGuard) are not in the catalog: they clash with systemd-resolved.

## License

GPL-3.0-or-later: see [LICENSE](LICENSE). The logos of the services shown on the EgOS home page
belong to their owners and are not covered by this license: see [NOTICE](NOTICE).
