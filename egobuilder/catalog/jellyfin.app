{
  "name": "Jellyfin",
  "headline": "Jellyfin (media)",
  "category": "Photos and media",
  "description": "Movies, series and music streamed to TV, phone and PC.",
  "build_var": "JELLYFIN_VERSION",
  "default_version": "12.1",
  "ports": {
    "web": {
      "default": 8096,
      "proto": "tcp",
      "label": "Web"
    },
    "discovery": {
      "default": 7358,
      "proto": "udp",
      "label": "Discovery client"
    }
  },
  "home_dirs": [
    "app/jellyfin/config",
    "app/jellyfin/cache"
  ],
  "stage_files": [
    "jellsrv.json",
    "jellremot.json"
  ],
  "credentials": {
    "file": "jelluser.json",
    "fields": {
      "Name": "user",
      "Password": "password"
    }
  },
  "wizard_order": {
    "wait": 1,
    "setup": 2,
    "verify": 1
  },
  "autosetup": "Completes the startup wizard: server name, language, admin = the EgOS user.",
  "options": {
    "server_name": {
      "label": "Server name",
      "default": "EgoFin"
    }
  },
  "homepage": {
    "icon": "jellyfin.png",
    "description": "Movies, series and music"
  }
}
--- compose
  jellyfin:
    image: jellyfin/jellyfin:${JELLYFIN_VERSION:-latest}
    container_name: jellyfin
    ports:
      - {{port.web}}:8096/tcp
      - {{port.discovery}}:7358/udp
    volumes:
      - ${JELLYFIN_CONFIG_LOCATION}:/config
      - ${JELLYFIN_CACHE_LOCATION}:/cache
      # paths consistent with the dir tree created by dirtree.sh
      - type: bind
        source: ${JELLYFIN_MEDIA_LOCATION}/Films
        target: /Films
      - type: bind
        source: ${JELLYFIN_MEDIA_LOCATION}/Musics
        target: /Musics
      - type: bind
        source: ${JELLYFIN_MEDIA_LOCATION}/SerieTv
        target: /Serie
        read_only: true
      - type: bind
        source: ${JELLYFIN_MEDIA_LOCATION}/ROMs
        target: /ROMs
        read_only: true
      # pool gate: /run/egos/pool-ok exists only while every pool disk is present
      # (egos-pool.service). create_host_path: false = docker must NOT create it
      # (compose would otherwise pass it as a legacy bind, which docker creates):
      # with a disk missing Jellyfin cannot start, not even docker's own restart
      # at boot, so its library scan never sees missing titles
      - type: bind
        source: /run/egos/pool-ok
        target: /egos-pool-ok
        read_only: true
        bind:
          create_host_path: false
    restart: 'unless-stopped'
    environment:
      - JELLYFIN_PublishedServerUrl=http://localhost:8096
    extra_hosts:
      - 'host.docker.internal:host-gateway'
--- env
# ---- Jellyfin ----
JELLYFIN_CONFIG_LOCATION=__HOME__/app/jellyfin/config
JELLYFIN_CACHE_LOCATION=__HOME__/app/jellyfin/cache
JELLYFIN_MEDIA_LOCATION=/srv/egos/pool/media
JELLYFIN_VERSION={{version}}
--- wizard-wait
echo "Waiting for Jellyfin to be ready (/health)..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:{{port.web}}/health)" = "200" ]; do
  echo -n "."; sleep 1
done
echo

--- wizard-setup
# the Startup API answers 401 once the wizard is complete: on a re-run
# (deploy-compose retries at every boot until this script succeeds) waiting
# for a 200 there would never end, so ask Jellyfin whether it is done
until INFO=$(curl -fsS http://localhost:{{port.web}}/System/Info/Public); do
  echo -n "."; sleep 1
done
if printf '%s' "$INFO" | grep -q '"StartupWizardCompleted":true'; then
echo "Jellyfin Startup wizard already completed in a previous run, skipping it"
else
echo "Waiting for the Jellyfin Startup wizard to be reachable..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:{{port.web}}/Startup/Configuration)" = "200" ]; do
  echo -n "."; sleep 1
done
echo

echo "JELL SRVSET - server name, country and language"
curl -fsS -X POST "http://localhost:{{port.web}}/Startup/Configuration" \
     -H "Content-Type: application/json" \
     -d @jellsrv.json

# GET on the user wizard is required before the POST
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:{{port.web}}/Startup/User)" = "200" ]; do
  echo -n "."; sleep 1
done
echo
echo "JELL USERSET - admin user and password"
curl -fsS -X POST "http://localhost:{{port.web}}/Startup/User" \
     -H "Content-Type: application/json" \
     -d @jelluser.json

echo
echo "JELL ACCESSET"
curl -fsS -X POST "http://localhost:{{port.web}}/Startup/RemoteAccess" \
     -H "Content-Type: application/json" \
     -d @jellremot.json

echo
echo "--- Complete ---"
curl -fsS -X POST "http://localhost:{{port.web}}/Startup/Complete"
fi

--- wizard-verify
echo "--- Verify ---"
curl -s "http://localhost:{{port.web}}/System/Info/Public"
echo
