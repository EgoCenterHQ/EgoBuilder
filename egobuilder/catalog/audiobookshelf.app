{
  "name": "Audiobookshelf",
  "headline": "Audiobookshelf (audiobooks)",
  "category": "Photos and media",
  "description": "Audiobooks and podcasts with phone apps and synced listening progress.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 13378,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "pool_dirs": [
    "media/Audiobooks",
    "media/Podcasts"
  ],
  "home_dirs": [
    "app/audiobookshelf/config",
    "app/audiobookshelf/metadata"
  ],
  "firstrun": "Create the root user on first visit.",
  "homepage": {
    "icon": "audiobookshelf.png",
    "description": "Audiobooks and podcasts"
  }
}
--- compose
  audiobookshelf:
    image: ghcr.io/advplyr/audiobookshelf:${AUDIOBOOKSHELF_VERSION:-latest}
    container_name: audiobookshelf
    ports:
      - '{{port.web}}:80'
    environment:
      - TZ=${TZ}
    volumes:
      - /srv/egos/pool/media/Audiobooks:/audiobooks
      - /srv/egos/pool/media/Podcasts:/podcasts
      - ${AUDIOBOOKSHELF_DATA_LOCATION}/config:/config
      - ${AUDIOBOOKSHELF_DATA_LOCATION}/metadata:/metadata
    restart: unless-stopped
--- env
# ---- Audiobookshelf ----
AUDIOBOOKSHELF_DATA_LOCATION=__HOME__/app/audiobookshelf
AUDIOBOOKSHELF_VERSION={{version}}
