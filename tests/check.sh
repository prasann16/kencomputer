#!/bin/bash
# What must pass before a change merges or a release is built:
# the unit tests, and Ken's native parts load and can decode a voice note.
#   tests/check.sh [python]      (default: .venv/bin/python)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${1:-.venv/bin/python}"
"$PY" -m pytest tests -q --asyncio-mode=auto
"$PY" desktop/checks/parts.py
node --test tests/desktop.test.cjs
