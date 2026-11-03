{
  "name": "Vaultwarden",
  "headline": "Vaultwarden (passwords)",
  "category": "Security and home",
  "description": "Password manager compatible with the Bitwarden apps.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 8222,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "home_dirs": [
    "app/vaultwarden/data"
  ],
  "options": {
    "signups": {
      "label": "Open sign-up of new accounts",
      "default": "true",
      "choices": [
        "true",
        "false"
      ]
    }
  },
  "notes": "The web vault and the apps need HTTPS: put a reverse proxy with a certificate in front.",
  "firstrun": "Register your account from the web vault, then turn sign-ups off.",
  "homepage": {
    "icon": "vaultwarden.png",
    "description": "Passwords"
  }
}
--- compose
  vaultwarden:
    image: docker.io/vaultwarden/server:${VAULTWARDEN_VERSION:-latest}
    container_name: vaultwarden
    ports:
      - '{{port.web}}:80'
    environment:
      - SIGNUPS_ALLOWED={{option.signups}}
      - TZ=${TZ}
    volumes:
      - ${VAULTWARDEN_DATA_LOCATION}:/data
    restart: unless-stopped
--- env
# ---- Vaultwarden ----
VAULTWARDEN_DATA_LOCATION=__HOME__/app/vaultwarden/data
VAULTWARDEN_VERSION={{version}}
