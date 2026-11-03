# EgoBuilder: the web app + everything the amd64 ISO build needs, in one image.
#
#   docker compose up -d --build                 -> http://localhost:8765
#   docker build --network=host -t egobuilder . && docker run -d --name egobuilder --privileged \
#       --network=host -v egobuilder-data:/data egobuilder
#
# --privileged is needed only to build ISOs (live-build mounts /proc and creates
# device nodes in its chroot); without it the app works and stops at "Prepare the tree".
# Jobs (generated trees, logs, ISOs) live in the /data volume.
# Host network (the bridge cannot reach the internet on some hosts), so the server
# listens on the host's 127.0.0.1 only.
FROM docker.io/library/debian:trixie

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 openssl make live-build curl ca-certificates gnupg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/egobuilder
COPY . .

ENV EGOBUILDER_LOCAL_BUILD=1 \
    EGOBUILDER_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/meta', timeout=4)"

CMD ["python3", "egobuilder/server.py", "--host", "127.0.0.1", "--port", "8765"]
