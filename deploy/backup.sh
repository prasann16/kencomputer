#!/usr/bin/env bash
# stdout is exclusively the archive stream. Never print status or secrets there.
set -euo pipefail
[ "$(id -u)" = 0 ]
[ -f /home/ken/.ken/.env ]
ken_uid=$(id -u ken)
svc() { runuser -u ken -- env XDG_RUNTIME_DIR="/run/user/$ken_uid" systemctl --user "$@" ken; }
restart=0
if svc is-active --quiet; then restart=1; fi
cleanup() {
  if [ "$restart" = 1 ]; then svc start >&2; fi
}
trap cleanup EXIT
svc stop >&2
# Other user-created services may still write files. For fully consistent machine-wide
# recovery, use provider snapshots or coordinate stopping those services separately.
tar --exclude='./.cache' --exclude='./.ken/venv' -czf - -C /home/ken .
