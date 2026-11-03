{
  "name": "Immich",
  "headline": "Immich (photos)",
  "category": "Photos and media",
  "description": "Automatic photo and video backup from your phone, like Google Photos but at home.",
  "build_var": "IMMICH_VERSION",
  "default_version": "v3.2.2",
  "ports": {
    "web": {
      "default": 2283,
      "proto": "tcp",
      "label": "Web"
    }
  },
  "secrets": {
    "db_password": "Postgres database password"
  },
  "pool_dirs": [
    "immich/upload"
  ],
  "home_dirs": [
    "app/immich/postgres"
  ],
  "named_volumes": [
    "model-cache"
  ],
  "credentials": {
    "file": "immichset.json",
    "fields": {
      "email": "email",
      "password": "password",
      "name": "user"
    }
  },
  "wizard_order": {
    "wait": 2,
    "setup": 1
  },
  "autosetup": "Creates the admin with the EgOS user's email and password.",
  "compose_note": "#\n# Based on Immich's OFFICIAL docker-compose.yml:\n# https://github.com/immich-app/immich/blob/main/docker/docker-compose.yml\n#\n",
  "homepage": {
    "icon": "immich.png",
    "description": "Photos and videos"
  }
}
--- compose
  immich-server:
    container_name: immich_server
    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}
    volumes:
      - ${UPLOAD_LOCATION}:/data
      - /etc/localtime:/etc/localtime:ro
    env_file:
      - .env
    ports:
      - '{{port.web}}:2283'
    depends_on:
      - redis
      - database
    restart: always
    healthcheck:
      disable: false

  immich-machine-learning:
    container_name: immich_machine_learning
    # For ML hardware acceleration: append -[armnn, cuda, rocm, openvino, rknn]
    # to the image tag, e.g. ${IMMICH_VERSION:-release}-cuda
    image: ghcr.io/immich-app/immich-machine-learning:${IMMICH_VERSION:-release}
    volumes:
      - model-cache:/cache
    env_file:
      - .env
    restart: always
    healthcheck:
      disable: false

  redis:
    container_name: immich_redis
    image: docker.io/valkey/valkey:9@sha256:8436e10bc65c94886a91d4415b6a6dfa9cb5a306fb3b996e5bb67cd2b4854193
    healthcheck:
      test: redis-cli ping || exit 1
    restart: always

  database:
    container_name: immich_postgres
    image: ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_USER: ${DB_USERNAME}
      POSTGRES_DB: ${DB_DATABASE_NAME}
      POSTGRES_INITDB_ARGS: '--data-checksums'
    volumes:
      # WARNING (Immich docs): path on a local POSIX filesystem (ext4/zfs/xfs/btrfs),
      # NEVER on an NFS/SMB network share.
      - ${DB_DATA_LOCATION}:/var/lib/postgresql/data
    shm_size: 128mb
    restart: always
    healthcheck:
      disable: false
--- env
# ---- Immich ----
UPLOAD_LOCATION=/srv/egos/pool/immich/upload
# MUST be on a local POSIX filesystem, not NFS/SMB
DB_DATA_LOCATION=__HOME__/app/immich/postgres
DB_USERNAME=postgres
DB_PASSWORD={{secret.db_password}}
DB_DATABASE_NAME=immich
IMMICH_VERSION={{version}}
--- wizard-wait
echo "Waiting for Immich to be ready (/api/server/ping)..."
until curl -fsS -m 2 http://localhost:{{port.web}}/api/server/ping 2>/dev/null | grep -q pong; do
  echo -n "."; sleep 1
done
echo
--- wizard-setup
echo "IMMICH USERSET"
curl -fsS -X POST "http://localhost:{{port.web}}/api/auth/admin-sign-up" \
     -H "Content-Type: application/json" \
     -d @immichset.json \
  || echo "(admin-sign-up failed: admin probably already created in a previous run, continuing)"
echo
echo "ok IMMICH"

