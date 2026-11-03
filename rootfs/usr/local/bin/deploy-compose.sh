#!/bin/bash
set -e
[ -f /var/lib/setup-data.json ] || { echo "No setup data"; exit 1; }

/usr/local/bin/dirtree.sh
# dirtree execs wizardall2.sh: if the wizard fails, set -e stops here
# and the service retries at the next boot (flag not touched)

touch /var/lib/docker-compose-done
systemctl disable docker-compose-startup.service
