{
  "name": "Paperless-ngx",
  "headline": "Paperless-ngx (documents)",
  "category": "Cloud and documents",
  "description": "Document archive with OCR: searchable scans and PDFs. Inbox folder in the pool.",
  "default_version": "latest",
  "ports": {
    "web": {
      "default": 8010,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "secrets": {
    "secret_key": "Django secret key"
  },
  "pool_dirs": [
    "documents/consume",
    "documents/media",
    "documents/export"
  ],
  "home_dirs": [
    "app/paperless/data"
  ],
  "autosetup": "Admin = the EgOS user and password; OCR in the system language.",
  "homepage": {
    "icon": "paperless-ngx.png",
    "description": "Documents"
  }
}
--- compose
  paperless-redis:
    image: docker.io/library/redis:7
    container_name: paperless_redis
    restart: unless-stopped

  paperless:
    image: ghcr.io/paperless-ngx/paperless-ngx:${PAPERLESS_VERSION:-latest}
    container_name: paperless
    depends_on:
      - paperless-redis
    ports:
      - '{{port.web}}:8000'
    environment:
      - PAPERLESS_REDIS=redis://paperless-redis:6379
      - PAPERLESS_SECRET_KEY=${PAPERLESS_SECRET_KEY}
      - PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER}
      - PAPERLESS_ADMIN_PASSWORD=${PAPERLESS_ADMIN_PASSWORD}
      - PAPERLESS_TIME_ZONE=${TZ}
      - PAPERLESS_OCR_LANGUAGE={{lang3}}
      - USERMAP_UID=1000
      - USERMAP_GID=1000
    volumes:
      - ${PAPERLESS_DATA_LOCATION}:/usr/src/paperless/data
      - /srv/egos/pool/documents/media:/usr/src/paperless/media
      - /srv/egos/pool/documents/consume:/usr/src/paperless/consume
      - /srv/egos/pool/documents/export:/usr/src/paperless/export
    restart: unless-stopped
--- env
# ---- Paperless-ngx ----
PAPERLESS_DATA_LOCATION=__HOME__/app/paperless/data
PAPERLESS_SECRET_KEY={{secret.secret_key}}
PAPERLESS_ADMIN_USER=__USER__
PAPERLESS_ADMIN_PASSWORD=__PASSWORD__
PAPERLESS_VERSION={{version}}
