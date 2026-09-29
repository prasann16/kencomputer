"""Build the bundled Python engine on the target OS before electron-builder."""
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
subprocess.run([
    str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'ken-engine',
    '--distpath', str(root / 'dist' / 'engine'), '--workpath', str(root / 'build' / 'engine'),
    '--specpath', str(root / 'build'), '--paths', str(root), '--collect-all', 'claude_agent_sdk',
    '--collect-submodules', 'mcp.server', '--collect-submodules', 'mcp.client', '--collect-submodules', 'aiohttp',
    '--collect-all', 'faster_whisper',
    '--add-data', f'{root / "web"}{os.pathsep}web',
    '--add-data', f'{root / "jobs.default.json"}{os.pathsep}.',
    str(root / 'desktop' / 'runtime.py'),
], check=True, cwd=root)
