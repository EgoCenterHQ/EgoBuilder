{
  "name": "Syncthing",
  "headline": "Syncthing (sync)",
  "category": "Cloud and documents",
  "description": "Sync folders between PCs and phones without a cloud. Shared folder in the pool.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 8384,
      "proto": "tcp",
      "label": "Web"
    },
    "sync": {
      "default": 22000,
      "proto": "tcp+udp",
      "label": "Transfer"
    },
    "discovery": {
      "default": 21027,
      "proto": "udp",
      "label": "Local discovery"
    }
  },
  "pool_dirs": [
    "sync"
  ],
  "home_dirs": [
    "app/syncthing"
  ],
  "firstrun": "Set a password for the web UI on first visit.",
  "homepage": {
    "icon": "syncthing.png",
    "description": "File sync"
  }
}
--- compose
  syncthing:
    image: docker.io/syncthing/syncthing:${SYNCTHING_VERSION:-latest}
    container_name: syncthing
    hostname: {{hostname}}
    environment:
      - PUID=1000
      - PGID=1000
    ports:
      - '{{port.web}}:8384'
      - '{{port.sync}}:22000/tcp'
      - '{{port.sync}}:22000/udp'
      - '{{port.discovery}}:21027/udp'
    volumes:
      - ${SYNCTHING_DATA_LOCATION}:/var/syncthing
      - /srv/egos/pool/sync:/var/syncthing/Sync
    restart: unless-stopped
--- env
# ---- Syncthing ----
SYNCTHING_DATA_LOCATION=__HOME__/app/syncthing
SYNCTHING_VERSION={{version}}
