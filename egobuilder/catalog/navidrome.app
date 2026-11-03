{
  "name": "Navidrome",
  "headline": "Navidrome (music)",
  "category": "Photos and media",
  "description": "Subsonic-compatible music server: play the Musics folder from apps and the browser.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 4533,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "home_dirs": [
    "app/navidrome/data"
  ],
  "firstrun": "Create the admin user on first visit.",
  "homepage": {
    "icon": "navidrome.png",
    "description": "Music"
  }
}
--- compose
  navidrome:
    image: docker.io/deluan/navidrome:${NAVIDROME_VERSION:-latest}
    container_name: navidrome
    user: 1000:1000
    ports:
      - '{{port.web}}:4533'
    environment:
      - ND_SCANSCHEDULE=1h
      - ND_DEFAULTLANGUAGE={{lang2}}
    volumes:
      - ${NAVIDROME_DATA_LOCATION}:/data
      - /srv/egos/pool/media/Musics:/music:ro
    restart: unless-stopped
--- env
# ---- Navidrome ----
NAVIDROME_DATA_LOCATION=__HOME__/app/navidrome/data
NAVIDROME_VERSION={{version}}
