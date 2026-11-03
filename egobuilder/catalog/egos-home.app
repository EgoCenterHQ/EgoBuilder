{
  "name": "EgosHome",
  "headline": "EgosHome (home page)",
  "category": "Tools",
  "recommended": true,
  "description": "Home page with a card for every app of the box, found by itself from Docker, plus Cockpit.",
  "default_version": "3.13-alpine",
  "ports": {
    "web": {
      "default": 8000,
      "proto": "tcp",
      "label": "Web (host network)"
    },
    "proxy": {
      "default": 23750,
      "proto": "tcp",
      "label": "Docker proxy (127.0.0.1 only)"
    }
  },
  "stage_dirs": [
    "egos-home"
  ],
  "autosetup": "Nothing to configure: one card per container with a web page, Cockpit always listed; egos.home.* labels to rename or hide a card.",
  "notes": "The Docker socket goes only into a proxy that allows just the container list (docker-proxy.cfg): no environment variables, logs or commands.",
  "homepage": {
    "description": "Home page"
  }
}
--- compose
  # EgosHome: home page with one card per container, read from Docker through
  # docker-proxy, which allows ONLY GET /containers/json (egos-home/docker-proxy.cfg:
  # no inspect = no environment variables, no logs, no POST)
  docker-proxy:
    container_name: egos-home-docker-proxy
    image: docker.io/tecnativa/docker-socket-proxy:0.3.0
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./egos-home/docker-proxy.cfg:/usr/local/etc/haproxy/haproxy.cfg.template:ro
    ports:
      - '127.0.0.1:{{port.proxy}}:2375'
    # not read_only: the image writes its haproxy.cfg next to the template at start
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    labels:
      - egos.home.enable=false
    restart: unless-stopped

  egos-home:
    container_name: egos-home
    image: docker.io/library/python:{{version}}
    # host network: the port probes on 127.0.0.1 see the system's ports
    network_mode: host
    user: '1000:1000'
    read_only: true
    environment:
      - PORT={{port.web}}
      - FALLBACK_HOST={{hostname}}.local
      - DOCKER_API=http://127.0.0.1:{{port.proxy}}
    volumes:
      - ./egos-home/home.py:/app/home.py:ro
      - ./egos-home/icons:/app/icons:ro
    command: ['python', '-u', '/app/home.py']
    depends_on:
      - docker-proxy
    labels:
      - egos.home.enable=false
    healthcheck:
      test: ['CMD', 'python', '-c', "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=3)"]
      interval: 60s
      timeout: 5s
      retries: 3
    restart: unless-stopped
--- env
# ---- EgosHome ----
