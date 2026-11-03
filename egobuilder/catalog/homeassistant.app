{
  "name": "Home Assistant",
  "headline": "Home Assistant (home automation)",
  "category": "Security and home",
  "description": "Local home automation: lights, sensors, automations. Uses the host network to discover devices.",
  "default_version": "stable",
  "ports": {
    "web": {
      "default": 8123,
      "proto": "tcp",
      "label": "Web",
      "fixed": true
    }
  },
  "home_dirs": [
    "app/homeassistant/config"
  ],
  "firstrun": "Create the owner user on first visit.",
  "homepage": {
    "icon": "home-assistant.png",
    "description": "Home automation"
  }
}
--- compose
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:${HOMEASSISTANT_VERSION:-stable}
    container_name: homeassistant
    network_mode: host
    environment:
      - TZ=${TZ}
    volumes:
      - ${HOMEASSISTANT_CONFIG_LOCATION}:/config
      - /etc/localtime:/etc/localtime:ro
      - /run/dbus:/run/dbus:ro
    restart: unless-stopped
--- env
# ---- Home Assistant ----
HOMEASSISTANT_CONFIG_LOCATION=__HOME__/app/homeassistant/config
HOMEASSISTANT_VERSION={{version}}
