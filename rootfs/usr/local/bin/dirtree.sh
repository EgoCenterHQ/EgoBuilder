#!/bin/bash
set -e

STAGE="/usr/local/share/stack"
U=$(python3 -c "import json; print(json.load(open('/var/lib/setup-data.json'))['user'])")
H="/home/$U"

[ -d "$H" ] || { echo "Home of $U not found"; exit 1; }

# media and photos live on the storage pool (egos-pool.service) from the start:
# adding a disk later never moves data. Without the pool, stop: the folders
# would end up under an empty mount point.
POOL=/srv/egos/pool
findmnt -rno TARGET "$POOL" >/dev/null || { echo "Storage pool $POOL not mounted"; exit 1; }

mkdir -p "$H/app/repos"
mkdir -p "$POOL/media/Films"
mkdir -p "$POOL/media/Musics"
mkdir -p "$POOL/media/SerieTv"
mkdir -p "$POOL/media/ROMs"
mkdir -p "$POOL/media/downloads"
mkdir -p "$POOL/immich/upload"
mkdir -p "$H/app/jellyfin/config"
mkdir -p "$H/app/jellyfin/cache"
mkdir -p "$H/app/immich/postgres"
chown -R "$U:$U" "$POOL/media" "$POOL/immich"

# cp (not mv): if the service fails and restarts at the next boot, the sources are still there
cp "$STAGE/docker-compose.yml" "$H/app/repos/"
cp "$STAGE/wizardall2.sh"      "$H/app/repos/"
cp "$STAGE/jellsrv.json"       "$H/app/repos/"
cp "$STAGE/jellremot.json"     "$H/app/repos/"
cp -rT "$STAGE/egos-home" "$H/app/repos/egos-home"

# REQ.3: generate the credential json files for the wizardall2.sh POSTs
# from the credentials collected by the webapp at 1st boot
python3 - "$H" <<'PY'
import json, sys
h = sys.argv[1]
d = json.load(open('/var/lib/setup-data.json'))
json.dump({"email": d["email"], "password": d["password"], "name": d["user"]},
          open(h + "/app/repos/immichset.json", "w"))
json.dump({"Name": d["user"], "Password": d["password"]},
          open(h + "/app/repos/jelluser.json", "w"))
PY

# generate .env with absolute paths: compose does NOT expand $HOME inside .env
sed "s|__HOME__|$H|g" "$STAGE/env.template" > "$H/app/repos/.env"

chmod +x "$H/app/repos/wizardall2.sh"
chown -R "$U:$U" "$H/app"

# --- SMB: upload media from any device (\\egocenter.local\media) ---
# SMBCONF heredoc NOT quoted: $H and $U expand at runtime.
# cat > and smbpasswd -a are idempotent when the service retries at the next boot.
PASS=$(python3 -c "import json; print(json.load(open('/var/lib/setup-data.json'))['password'])")

cat > /etc/samba/smb.conf <<SMBCONF
[global]
   server string = EgoCenter
   server min protocol = SMB2
   map to guest = never

[media]
   path = $POOL/media
   valid users = $U
   read only = no
   create mask = 0664
   directory mask = 0775
SMBCONF

printf '%s\n%s\n' "$PASS" "$PASS" | smbpasswd -s -a "$U"

cat > /etc/avahi/services/smb.service <<'SMBAV'
<?xml version="1.0"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">EgoCenter</name>
  <service>
    <type>_smb._tcp</type>
    <port>445</port>
  </service>
</service-group>
SMBAV

systemctl restart smbd avahi-daemon
# wsdd2 has BindsTo on the smbd unit: stopping smbd stops it and BindsTo
# does not propagate the restart. Explicit restart; || true because discovery
# is cosmetic and its failure must not block the stack (set -e).
systemctl restart wsdd2 || true

echo "PATHS CREATED - starting compose wizard"
cd "$H/app/repos"
exec ./wizardall2.sh
