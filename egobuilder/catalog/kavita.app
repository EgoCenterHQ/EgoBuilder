{
  "name": "Kavita",
  "headline": "Kavita (books)",
  "category": "Photos and media",
  "description": "Library of ebooks, comics and manga to read in the browser.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 5000,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "pool_dirs": [
    "media/Books"
  ],
  "home_dirs": [
    "app/kavita/config"
  ],
  "firstrun": "Create the admin user on first visit.",
  "homepage": {
    "icon": "kavita.png",
    "description": "Books and comics"
  }
}
--- compose
  kavita:
    image: docker.io/jvmilazz0/kavita:${KAVITA_VERSION:-latest}
    container_name: kavita
    ports:
      - '{{port.web}}:5000'
    environment:
      - TZ=${TZ}
    volumes:
      - ${KAVITA_CONFIG_LOCATION}:/kavita/config
      - /srv/egos/pool/media/Books:/books
    restart: unless-stopped
--- env
# ---- Kavita ----
KAVITA_CONFIG_LOCATION=__HOME__/app/kavita/config
KAVITA_VERSION={{version}}
