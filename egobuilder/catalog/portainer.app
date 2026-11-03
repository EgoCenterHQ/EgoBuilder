{
  "name": "Portainer CE",
  "headline": "Portainer (containers)",
  "category": "Tools",
  "description": "Web UI to manage Docker containers, images and volumes.",
  "default_version": "lts",
  "ports": {
    "web": {
      "default": 9443,
      "proto": "tcp",
      "label": "Web (https)"
    }
  },
  "home_dirs": [
    "app/portainer/data"
  ],
  "firstrun": "Create the admin within 5 minutes of startup (otherwise restart the container).",
  "homepage": {
    "icon": "portainer.png",
    "description": "Docker containers",
    "scheme": "https"
  }
}
--- compose
  portainer:
    image: docker.io/portainer/portainer-ce:${PORTAINER_VERSION:-lts}
    container_name: portainer
    ports:
      - '{{port.web}}:9443'
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ${PORTAINER_DATA_LOCATION}:/data
    restart: unless-stopped
--- env
# ---- Portainer ----
PORTAINER_DATA_LOCATION=__HOME__/app/portainer/data
PORTAINER_VERSION={{version}}
