#!/usr/bin/env bash
# Read-only checks, also run before uploading a release.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Use a root SSH account for provisioning.' >&2; exit 1; }
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || {
  echo 'This installer requires a fresh Ubuntu 24.04 VM.' >&2; exit 1;
}
for existing in /opt/ken /root/ken-assistant /root/.ken /home/*/.ken/.env; do
  if [ -e "$existing" ] || [ -L "$existing" ]; then
    echo "Existing Ken installation detected at $existing. Refusing to provision this box." >&2
    exit 1
  fi
done
if id ken >/dev/null 2>&1 && [ ! -f /etc/ken-provisioned ]; then
  echo 'An unmanaged ken user already exists. Refusing to modify it.' >&2
  exit 1
fi
