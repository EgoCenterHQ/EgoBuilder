{
  "name": "Uptime Kuma",
  "headline": "Uptime Kuma (monitoring)",
  "category": "Tools",
  "description": "Checks that sites and services are reachable and alerts you when they go down.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 3001,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "home_dirs": [
    "app/uptimekuma/data"
  ],
  "firstrun": "Create the admin user on first visit.",
  "homepage": {
    "icon": "uptime-kuma.png",
    "description": "Monitoring"
  }
}
--- compose
  uptimekuma:
    image: docker.io/louislam/uptime-kuma:${UPTIMEKUMA_VERSION:-latest}
    container_name: uptimekuma
    ports:
      - '{{port.web}}:3001'
    volumes:
      - ${UPTIMEKUMA_DATA_LOCATION}:/app/data
    restart: unless-stopped
--- env
# ---- Uptime Kuma ----
UPTIMEKUMA_DATA_LOCATION=__HOME__/app/uptimekuma/data
UPTIMEKUMA_VERSION={{version}}
