"""Bundle Ken's engine (bot.py, the same one Telegram runs) for Ken.app. Run on the target Mac arch."""
import os
import platform
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
arch = {'x86_64': 'x64', 'arm64': 'arm64'}[platform.machine()]  # electron-builder's arch names
data = lambda src, dest='.': ['--add-data', f'{root / src}{os.pathsep}{dest}']
subprocess.run([
    str(root / '.venv' / 'bin' / 'python'), '-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'ken-engine',
    '--distpath', str(root / 'dist' / f'engine-{arch}'), '--workpath', str(root / 'build' / 'engine'),
    '--specpath', str(root / 'build'), '--paths', str(root),
    # Only data files the code can't import: the bundled `claude` CLI and Whisper's assets.
    '--collect-data', 'claude_agent_sdk', '--collect-data', 'faster_whisper',
    # Optional extras Ken never uses: Whisper's silence filter (off in voice.py) and a download accelerator.
    '--exclude-module', 'onnxruntime', '--exclude-module', 'hf_xet',
    *data('web', 'web'), *data('SOUL.template.md'), *data('jobs.default.json'),
    str(root / 'bot.py'),
], check=True, cwd=root)
