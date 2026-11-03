{
  "name": "File Browser",
  "headline": "File Browser (files)",
  "category": "Cloud and documents",
  "description": "Manage the pool's files from the browser: upload, download, share links.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 8085,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "home_dirs": [
    "app/filebrowser/database",
    "app/filebrowser/config"
  ],
  "firstrun": "User admin; the initial password is in the log: docker logs filebrowser.",
  "homepage": {
    "icon": "filebrowser.png",
    "description": "Pool files"
  }
}
--- compose
  filebrowser:
    image: docker.io/filebrowser/filebrowser:${FILEBROWSER_VERSION:-latest}
    container_name: filebrowser
    user: 1000:1000
    ports:
      - '{{port.web}}:80'
    volumes:
      - /srv/egos/pool:/srv
      - ${FILEBROWSER_DATA_LOCATION}/database:/database
      - ${FILEBROWSER_DATA_LOCATION}/config:/config
    restart: unless-stopped
--- env
# ---- File Browser ----
FILEBROWSER_DATA_LOCATION=__HOME__/app/filebrowser
FILEBROWSER_VERSION={{version}}
