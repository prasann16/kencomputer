#!/usr/bin/env bash
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run this check through the operator's root SSH account." >&2; exit 1; }
ken_uid=$(id -u ken)
[ "$ken_uid" != 0 ]
[ "$(loginctl show-user ken -p Linger --value)" = yes ]
runuser -u ken -- env XDG_RUNTIME_DIR="/run/user/$ken_uid" systemctl --user is-enabled --quiet ken
runuser -u ken -- env XDG_RUNTIME_DIR="/run/user/$ken_uid" systemctl --user is-active --quiet ken
[ "$(stat -c %a /home/ken/.ken/.env)" = 600 ]
runuser -u ken -- /home/ken/.ken/venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
import json
config = dotenv_values('/home/ken/.ken/.env')
assert config.get('TELEGRAM_BOT_TOKEN'), 'Missing Telegram token'
assert int(config.get('ALLOWED_USER_ID', '0')) > 0, 'Missing Telegram owner'
assert config.get('ANTHROPIC_API_KEY'), 'Missing model API key'
assert config.get('KEN_NO_AUTOUPDATE') == '1', 'Bundled release must not auto-pull source'
assert Path('/home/ken/.ken/work/SOUL.md').is_file(), 'Missing memory file'
assert isinstance(json.loads(Path('/home/ken/.ken/jobs.json').read_text()), list)
assert Path('/home/ken/.ken/hosting.md').is_file(), 'Missing hosting note'
assert isinstance(json.loads(Path('/home/ken/.ken/setup.json').read_text()), dict), 'Missing setup checklist'
print('Configuration, owner, memory, and schedule files are present; credentials withheld.')
PY
timedatectl show -p Timezone
df -h /home/ken
echo 'Local service checks passed. Send a Telegram message and voice note to verify the full path.'
echo 'Then reboot from your provider console, reconnect, and run this check again.'
