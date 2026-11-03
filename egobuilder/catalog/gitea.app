{
  "name": "Gitea",
  "headline": "Gitea (git)",
  "category": "Tools",
  "description": "Private git repositories with a web UI, issues and pull requests.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 3002,
      "proto": "tcp",
      "label": "Web"
    },
    "ssh": {
      "default": 2222,
      "proto": "tcp",
      "label": "SSH git"
    }
  },
  "home_dirs": [
    "app/gitea/data"
  ],
  "firstrun": "Install page on first visit (SQLite is fine).",
  "homepage": {
    "icon": "gitea.png",
    "description": "Git repositories"
  }
}
--- compose
  gitea:
    image: docker.io/gitea/gitea:${GITEA_VERSION:-latest}
    container_name: gitea
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__server__SSH_PORT={{port.ssh}}
    ports:
      - '{{port.web}}:3000'
      - '{{port.ssh}}:22'
    volumes:
      - ${GITEA_DATA_LOCATION}:/data
      - /etc/localtime:/etc/localtime:ro
    restart: unless-stopped
--- env
# ---- Gitea ----
GITEA_DATA_LOCATION=__HOME__/app/gitea/data
GITEA_VERSION={{version}}
