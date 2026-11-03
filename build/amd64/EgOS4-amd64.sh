#!/bin/bash
# =============================================================================
# EgOS4-amd64.sh — ISO Debian 13 (trixie) unattended + docker compose + stack Immich/Jellyfin.
# Produces egos-<version>-amd64.iso (live-build iso-hybrid, d-i in "live" mode:
#   the installer copies the live squashfs to the disk, then applies the preseed).
#
# Only the amd64-specific steps are here. The files EgOS adds to the system are
# the repo tree (rootfs/ stack/ webapp/ extras/ + build/amd64/overlay/), copied by
# egos_overlay (build/common.sh); live-build config = build/amd64/live-build/.
# Version: VERSION (repo root).
#
# USAGE (host, e.g. Arch) — `make iso-amd64` does exactly this under out/amd64:
#   mkdir -p ~/egos-amd64 && cd ~/egos-amd64       # empty work directory
#   sudo podman run -it --rm --privileged --network=host \
#     -v "$PWD:/build:Z" -v /path/to/egos:/src:ro -w /build debian:trixie bash
#   # inside the container:
#   apt-get update && apt-get install -y live-build curl ca-certificates gnupg
#   bash /src/build/amd64/EgOS4-amd64.sh 2>&1 | tee /build/build.log
#
# RESULTING FLOW:
#   USB -> unattended d-i install -> poweroff (remove USB)
#   1st boot -> egos-sshkeys -> first-boot-setup.service -> webapp :80
#              (setupbox.local) -> credentials -> linux user -> REBOOT
#   2nd boot -> time-sync + DNS -> docker-compose-startup.service
#              -> dirtree.sh -> wizardall2.sh               (egocenter.local)
#
# SERVICES: webapp :80 (1st boot only) | Immich :2283 | Jellyfin :8096 + 7358/udp
#          Cockpit :9090 (https, login with the user created in setup) | Samba | SSH
#
# NB rebuild: run `lb clean` in the same directory first.
# =============================================================================
set -e
. "$(dirname "$0")/../common.sh"
egos_check_workdir

ISO="egos-${EGOS_VERSION}-amd64.iso"
OVL=config/includes.chroot     # live-build: copied as-is into the squashfs
KEYS=keys

for c in lb curl gpg apt-get; do
    command -v "$c" >/dev/null || { echo "ERROR: $c missing (apt-get install -y live-build curl ca-certificates gnupg)"; exit 1; }
done
echo "EgOS $EGOS_VERSION amd64 - build $BUILD_DATE"

# -----------------------------------------------------------------------------
# 1. lb config — Debian 13 (trixie)
# -----------------------------------------------------------------------------
lb config \
    --distribution trixie \
    --architecture amd64 \
    --debian-installer live \
    --debian-installer-distribution trixie \
    --debian-installer-gui false \
    --archive-areas "main contrib non-free non-free-firmware" \
    --firmware-chroot true \
    --firmware-binary true \
    --binary-images iso-hybrid \
    --iso-volume "EGOS_${EGOS_VERSION//./_}" \
    --mode debian

# -----------------------------------------------------------------------------
# 2. Repo key: Docker, fingerprint-checked.
#    Used here (build container) to download the .debs, and shipped in the
#    image so the installed system keeps the repo (security updates).
# -----------------------------------------------------------------------------
rm -rf "$OVL"                  # only EgOS files in there: no leftovers from an older build
mkdir -p "$KEYS"
DOCKER_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
curl -fsSL https://download.docker.com/linux/debian/gpg -o "$KEYS/docker.asc"
rm -f "$KEYS/docker.gpg"
gpg --dearmor -o "$KEYS/docker.gpg" "$KEYS/docker.asc"
gpg --show-keys --with-colons --with-fingerprint "$KEYS/docker.gpg" \
    | grep -q "^fpr:::::::::$DOCKER_FPR:" \
    || { echo "ERROR 2: fingerprint $DOCKER_FPR missing from the Docker key"; exit 1; }

mkdir -p "$OVL"/etc/apt/keyrings "$OVL"/usr/local/share/egos/apt
cp "$KEYS/docker.gpg" "$OVL"/etc/apt/keyrings/
# NOT in sources.list.d during the build (live-build would use them in the
# chroot): d-i late_command copies them into the installed system
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian trixie stable" \
    > "$OVL"/usr/local/share/egos/apt/docker.list
echo "2 ok: key verified (Docker $DOCKER_FPR)"

# -----------------------------------------------------------------------------
# 3-5. live-build config from build/amd64/live-build/:
#   includes.installer/preseed.cfg      3. unattended preseed
#   package-lists/egos.list.chroot      4. live squashfs packages = installed system
#   hooks/normal/0400-egos.hook.chroot  4c. no ifupdown, no SSH host keys in the squashfs.
#                                          NEVER apt-get install there: packages added by a hook are
#                                          not in chroot.packages.install, so live-build lists them in
#                                          filesystem.packages-remove and d-i uninstalls them (sec. 13)
#   hooks/normal/0500-bootloader-default.hook.binary
#                                       5. default to the installer + 5s timeout, BIOS and UEFI;
#                                          every assumption checked, mismatch => build fails
# -----------------------------------------------------------------------------
egos_copy "$REPO/build/amd64/live-build" config

# -----------------------------------------------------------------------------
# 4b. Docker: .debs downloaded IN THE build CONTAINER and shipped
#     via config/packages.chroot (local install, no repos inside the chroot:
#     config/archives failed with "no installation candidate").
# -----------------------------------------------------------------------------
mkdir -p config/packages.chroot /etc/apt/keyrings
cp "$KEYS/docker.gpg" /etc/apt/keyrings/
cp "$OVL"/usr/local/share/egos/apt/docker.list /etc/apt/sources.list.d/
apt-get update
( cd config/packages.chroot && rm -f ./*.deb && \
  apt-get download docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin && \
  for f in *.deb; do case "$f" in (*%3a*) mv "$f" "$(printf '%s' "$f" | sed 's/%3a/./')";; esac; done )
echo "Debs shipped in packages.chroot:"
ls -l config/packages.chroot/

# -----------------------------------------------------------------------------
# 5b-11. EgOS files: the repo tree
# -----------------------------------------------------------------------------
egos_overlay "$OVL" amd64

# 12. Build
# -----------------------------------------------------------------------------
lb build

# -----------------------------------------------------------------------------
# 13. Checks (on the live chroot = installed
#     system) + anti-purge: filesystem.packages-remove is created AFTER the
#     hooks and lists what d-i removes at the end of the install.
# -----------------------------------------------------------------------------
ROOT=chroot
CRIT="docker-ce containerd.io docker-compose-plugin samba wsdd2 hostapd dnsmasq avahi-daemon cockpit-ws sudo openssh-server htop rsync mergerfs parted attr git python3-requests systemd-resolved"
for p in $CRIT; do
    chroot "$ROOT" dpkg-query -W -f='${Status}\n' "$p" 2>/dev/null | grep -q 'install ok installed' \
        || { echo "ERROR 13: $p not installed"; exit 1; }
done
for p in network-manager modemmanager wpasupplicant ppp ifupdown dhcpcd-base; do
    if chroot "$ROOT" dpkg-query -W -f='${Status}\n' "$p" 2>/dev/null | grep -q 'install ok installed'; then
        echo "ERROR 13: $p installed: only systemd-networkd may manage the network"; exit 1
    fi
done
if ls "$ROOT"/etc/ssh/ssh_host_* >/dev/null 2>&1; then
    echo "ERROR 13: SSH host keys present in the squashfs"; exit 1
fi
grep -qx "EGOS_VERSION=$EGOS_VERSION" "$ROOT/etc/egos-release" \
    || { echo "ERROR 13: /etc/egos-release missing or wrong version"; exit 1; }
grep -q 'egos-storage.sh' "$ROOT/etc/skel/.bashrc" \
    || { echo "ERROR 13: storage aliases missing from /etc/skel/.bashrc"; exit 1; }
for f in manifest.json index.html egos-storage.js egos-storage.css; do
    [ -s "$ROOT/usr/share/cockpit/egos-storage/$f" ] || { echo "ERROR 13: Cockpit page file $f missing"; exit 1; }
done
[ -s "$ROOT/etc/udev/rules.d/90-egos-reattach.rules" ] && [ -s "$ROOT/etc/systemd/system/egos-reattach.service" ] \
    || { echo "ERROR 14: automatic reattach (udev rule / unit) missing"; exit 1; }
for u in egos-pool.service egos-pool-guard.service; do
    [ -L "$ROOT/etc/systemd/system/multi-user.target.wants/$u" ] || [ -f "$OVL/etc/systemd/system/$u" ] \
        || { echo "ERROR 13: $u missing"; exit 1; }
done
grep -q '^JELLYFIN_MEDIA_LOCATION=/srv/egos/pool/media$' "$ROOT/usr/local/share/stack/env.template" \
    || { echo "ERROR 13: the stack does not start on the pool"; exit 1; }
grep -q "^IMMICH_VERSION=$IMMICH_VERSION$" "$ROOT/usr/local/share/stack/env.template" \
    || { echo "ERROR 13: env.template not pinned"; exit 1; }
PR=binary/live/filesystem.packages-remove
if [ -f "$PR" ]; then
    for p in $CRIT; do
        if grep -qx "$p" "$PR"; then
            echo "ERROR 13: $p in $PR — it would be uninstalled at the end of the install"; exit 1
        fi
    done
    echo "Packages d-i removes at the end of the install:"; tr '\n' ' ' < "$PR"; echo
fi
echo "13 ok: critical packages present, no network conflict, EgOS $EGOS_VERSION"

mv -f live-image-amd64.hybrid.iso "$ISO"
echo
echo "Done: $ISO"
echo "On the host: sudo chown \$USER:\$USER $ISO"
