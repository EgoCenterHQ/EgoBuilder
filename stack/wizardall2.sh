#!/bin/bash
# mentality 0 errors
set -e
cd "$(dirname "$0")"   # the curl -d @file.json calls use relative paths

echo "Starting compose"
docker compose -f docker-compose.yml up -d

echo "Waiting for Jellyfin to be ready (/health)..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8096/health)" = "200" ]; do
  echo -n "."; sleep 1
done
echo

echo "Waiting for Immich to be ready (/api/server/ping)..."
until curl -fsS -m 2 http://localhost:2283/api/server/ping 2>/dev/null | grep -q pong; do
  echo -n "."; sleep 1
done
echo
echo "Server up"

echo "IMMICH USERSET"
curl -fsS -X POST "http://localhost:2283/api/auth/admin-sign-up" \
     -H "Content-Type: application/json" \
     -d @immichset.json \
  || echo "(admin-sign-up failed: admin probably already created in a previous run, continuing)"
echo
echo "ok IMMICH"

# the Startup API answers 401 once the wizard is complete: on a re-run
# (deploy-compose retries at every boot until this script succeeds) waiting
# for a 200 there would never end, so ask Jellyfin whether it is done
until INFO=$(curl -fsS http://localhost:8096/System/Info/Public); do
  echo -n "."; sleep 1
done
if printf '%s' "$INFO" | grep -q '"StartupWizardCompleted":true'; then
echo "Jellyfin Startup wizard already completed in a previous run, skipping it"
else
echo "Waiting for the Jellyfin Startup wizard to be reachable..."
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8096/Startup/Configuration)" = "200" ]; do
  echo -n "."; sleep 1
done
echo

echo "JELL SRVSET - server name, country and language"
curl -fsS -X POST "http://localhost:8096/Startup/Configuration" \
     -H "Content-Type: application/json" \
     -d @jellsrv.json

# GET on the user wizard is required before the POST
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8096/Startup/User)" = "200" ]; do
  echo -n "."; sleep 1
done
echo
echo "JELL USERSET - admin user and password"
curl -fsS -X POST "http://localhost:8096/Startup/User" \
     -H "Content-Type: application/json" \
     -d @jelluser.json

echo
echo "JELL ACCESSET"
curl -fsS -X POST "http://localhost:8096/Startup/RemoteAccess" \
     -H "Content-Type: application/json" \
     -d @jellremot.json

echo
echo "--- Complete ---"
curl -fsS -X POST "http://localhost:8096/Startup/Complete"
fi

echo "--- Verify ---"
curl -s "http://localhost:8096/System/Info/Public"
echo
echo "ALL OK"
