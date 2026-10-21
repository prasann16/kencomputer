#!/bin/sh
# Run this checkout with its own data and port, alongside an installed Ken.
set -eu
cd "$(dirname "$0")"
export KEN_HOME="${KEN_HOME:-$PWD/.context/ken-local}"
export KEN_WEB_PORT="${KEN_WEB_PORT:-7788}"
export KEN_NO_AUTOUPDATE=1
mkdir -p "$KEN_HOME"
if [ ! -x .venv/bin/python ]; then
    echo 'Create the environment first: uv venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt' >&2
    exit 1
fi
exec .venv/bin/python bot.py
