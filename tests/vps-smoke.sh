#!/usr/bin/env bash
# Run only inside a disposable Ubuntu 24.04 Docker container, never on a server.
set -euo pipefail
[ -f /.dockerenv ] || { echo 'This smoke test must run in Docker.' >&2; exit 1; }
mkdir -p /tmp/ken-source
cp -R /source/. /tmp/ken-source/
bash /tmp/ken-source/deploy/preflight.sh

# An existing legacy installation must be refused without altering its data.
mkdir -p /opt/ken
printf 'do not change me' > /opt/ken/sentinel
if bash /tmp/ken-source/deploy/preflight.sh; then
  echo 'FAIL: legacy installation was not protected' >&2; exit 1
fi
[ "$(cat /opt/ken/sentinel)" = 'do not change me' ]
rm -rf /opt/ken

# Docker is not booted with systemd. Record those operations while exercising
# actual package installation, users, filesystem permissions, and source copying.
mkdir -p /usr/local/bin
for tool in loginctl systemctl timedatectl; do
  cat > "/usr/local/bin/$tool" <<'SH'
#!/bin/bash
printf '%s %s\n' "${0##*/}" "$*" >> /tmp/systemd-operations
SH
  chmod +x "/usr/local/bin/$tool"
done
bash /tmp/ken-source/deploy/bootstrap.sh Europe/Lisbon
test "$(id -u ken)" != 0
test "$(stat -c %U /home/ken/.ken/app/bot.py)" = ken
test "$(stat -c %a /home/ken)" = 700
test -f /etc/ken-provisioned
test ! -e /home/ken/.ken/.env
grep -q '^loginctl enable-linger ken$' /tmp/systemd-operations
grep -q '^timedatectl set-timezone Europe/Lisbon$' /tmp/systemd-operations
grep -q '^systemctl start user@' /tmp/systemd-operations
python3 -m venv /tmp/test-venv
/tmp/test-venv/bin/python -V
command -v ffmpeg

# Retrying an interrupted preparation is safe until owner configuration exists.
bash /tmp/ken-source/deploy/preflight.sh
printf 'test-only-sentinel' > /home/ken/.ken/.env
if bash /tmp/ken-source/deploy/bootstrap.sh Europe/Lisbon; then
  echo 'FAIL: configured customer was not protected' >&2; exit 1
fi
test "$(cat /home/ken/.ken/.env)" = 'test-only-sentinel'
echo 'PASS: Ubuntu prerequisites, non-root user, source ownership, and overwrite refusal.'
echo 'Systemd/timezone commands were recorded, not executed; VM acceptance remains required.'
