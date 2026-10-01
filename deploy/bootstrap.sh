#!/usr/bin/env bash
# Run by vps.py on a fresh Ubuntu 24.04 VM, as root. No customer credentials here.
set -euo pipefail
umask 077

[ "$(id -u)" = 0 ] || { echo "Connect using the new server's root SSH account." >&2; exit 1; }
. /etc/os-release
[ "$ID" = ubuntu ] && [ "$VERSION_ID" = 24.04 ] || {
  echo "This bootstrap currently supports Ubuntu 24.04 only." >&2; exit 1;
}
timezone=${1:?Pass an IANA timezone}
source_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
bash "$source_dir/deploy/preflight.sh"
case "$timezone" in
  /*|*..*) echo "Invalid timezone" >&2; exit 1 ;;
esac
if [ -e /home/ken/.ken/.env ]; then
  echo "Ken is already configured here. Refusing to replace an existing customer's installation." >&2
  exit 1
fi
if id ken >/dev/null 2>&1 && [ ! -f /etc/ken-provisioned ]; then
  echo "An unmanaged ken user already exists. Inspect this machine before continuing." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 python3-venv ffmpeg dbus-user-session tzdata
[ -f "/usr/share/zoneinfo/$timezone" ] || { echo "Unknown timezone: $timezone" >&2; exit 1; }
if ! id ken >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash ken
fi
[ "$(getent passwd ken | cut -d: -f6)" = /home/ken ] || { echo "Unexpected ken home" >&2; exit 1; }
chmod 700 /home/ken
touch /etc/ken-provisioned
chmod 600 /etc/ken-provisioned
timedatectl set-timezone "$timezone"
loginctl enable-linger ken
ken_uid=$(id -u ken)
systemctl start "user@$ken_uid.service"
install -d -m 700 -o ken -g ken /home/ken/.ken /home/ken/.ken/app
cp -R "$source_dir/." /home/ken/.ken/app/
chown -R ken:ken /home/ken/.ken/app
printf 'Prepared Ken user, source, boot startup, and timezone %s.\n' "$timezone"
