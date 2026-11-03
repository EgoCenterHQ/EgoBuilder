{
  "name": "Homepage",
  "headline": "Homepage (dashboard)",
  "category": "Tools",
  "description": "Start page with links to every app chosen here (generated automatically).",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 3000,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "home_dirs": [
    "app/homepage/config"
  ],
  "config_files": [
    {
      "name": "homepage-services.yaml",
      "dest": "app/homepage/config/services.yaml",
      "generate": "homepage_services"
    }
  ],
  "autosetup": "services.yaml lists the apps of this image (editable later).",
  "homepage": null
}
--- compose
  homepage:
    image: ghcr.io/gethomepage/homepage:${HOMEPAGE_VERSION:-latest}
    container_name: homepage
    ports:
      - '{{port.web}}:3000'
    environment:
      - HOMEPAGE_ALLOWED_HOSTS=*
      - PUID=1000
      - PGID=1000
    volumes:
      - ${HOMEPAGE_CONFIG_LOCATION}:/app/config
    restart: unless-stopped
--- env
# ---- Homepage ----
HOMEPAGE_CONFIG_LOCATION=__HOME__/app/homepage/config
HOMEPAGE_VERSION={{version}}
