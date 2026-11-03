{
  "name": "Nextcloud",
  "headline": "Nextcloud (cloud)",
  "category": "Cloud and documents",
  "description": "Files, calendar and contacts synced across all your devices (with MariaDB).",
  "default_version": "stable",
  "ports": {
    "web": {
      "default": 8080,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "secrets": {
    "db_password": "MariaDB user password",
    "db_root_password": "MariaDB root password"
  },
  "pool_dirs": [
    "nextcloud/data"
  ],
  "home_dirs": [
    "app/nextcloud/html",
    "app/nextcloud/db"
  ],
  "autosetup": "Automatic install: admin = the EgOS user and password.",
  "notes": "User data lives in the pool (/srv/egos/pool/nextcloud/data).",
  "homepage": {
    "icon": "nextcloud.png",
    "description": "Files, calendar, contacts"
  }
}
--- compose
  nextcloud-db:
    image: docker.io/library/mariadb:lts
    container_name: nextcloud_db
    command: --transaction-isolation=READ-COMMITTED
    environment:
      - MARIADB_ROOT_PASSWORD=${NEXTCLOUD_DB_ROOT_PASSWORD}
      - MARIADB_DATABASE=nextcloud
      - MARIADB_USER=nextcloud
      - MARIADB_PASSWORD=${NEXTCLOUD_DB_PASSWORD}
    volumes:
      - ${NEXTCLOUD_DB_LOCATION}:/var/lib/mysql
    restart: unless-stopped

  nextcloud:
    image: docker.io/library/nextcloud:${NEXTCLOUD_VERSION:-stable}
    container_name: nextcloud
    ports:
      - '{{port.web}}:80'
    environment:
      - MYSQL_HOST=nextcloud-db
      - MYSQL_DATABASE=nextcloud
      - MYSQL_USER=nextcloud
      - MYSQL_PASSWORD=${NEXTCLOUD_DB_PASSWORD}
      - NEXTCLOUD_ADMIN_USER=${NEXTCLOUD_ADMIN_USER}
      - NEXTCLOUD_ADMIN_PASSWORD=${NEXTCLOUD_ADMIN_PASSWORD}
      - NEXTCLOUD_TRUSTED_DOMAINS={{hostname}}.local {{hostname}} localhost
      - NEXTCLOUD_DATA_DIR=/var/www/data
      - PHP_UPLOAD_LIMIT=16G
    volumes:
      - ${NEXTCLOUD_HTML_LOCATION}:/var/www/html
      - ${NEXTCLOUD_DATA_LOCATION}:/var/www/data
    depends_on:
      - nextcloud-db
    restart: unless-stopped
--- env
# ---- Nextcloud ----
NEXTCLOUD_HTML_LOCATION=__HOME__/app/nextcloud/html
NEXTCLOUD_DB_LOCATION=__HOME__/app/nextcloud/db
NEXTCLOUD_DATA_LOCATION=/srv/egos/pool/nextcloud/data
NEXTCLOUD_DB_PASSWORD={{secret.db_password}}
NEXTCLOUD_DB_ROOT_PASSWORD={{secret.db_root_password}}
NEXTCLOUD_ADMIN_USER=__USER__
NEXTCLOUD_ADMIN_PASSWORD=__PASSWORD__
NEXTCLOUD_VERSION={{version}}
