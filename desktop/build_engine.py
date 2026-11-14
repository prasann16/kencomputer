"""Bundle Ken's engine (bot.py, the same one Telegram runs) for Ken.app. Run on the target Mac arch."""
import json
import os
import platform
import subprocess
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent.parent
arch = {'x86_64': 'x64', 'arm64': 'arm64'}[platform.machine()]  # electron-builder's arch names
data = lambda src, dest='.': ['--add-data', f'{root / src}{os.pathsep}{dest}']
python = str(root / '.venv' / 'bin' / 'python')

# Claude Code and Codex aren't in the app (~550 MB): Ken uses a copy on the computer or downloads the
# pinned one when it's first needed (runtimes.py). Pin this Mac's wheel of each, by checksum.
def pin(name, package):
    version = subprocess.run([python, '-c', f'import importlib.metadata as m; print(m.version("{package}"))'],
                             check=True, capture_output=True, text=True).stdout.strip()
    with urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{version}/json', timeout=60) as r:
        files = json.load(r)['urls']
    wheel = next(f for f in files if 'macosx' in f['filename'] and f['filename'].endswith(f"_{platform.machine()}.whl"))
    # A copy on the Mac is compared with the program's own version (Claude Code 2.1.300), never the
    # package's (claude-agent-sdk 0.2.167): that mix-up let a too-old Claude Code in.
    runs = subprocess.run([python, '-c', f"""import runtimes
from importlib.util import find_spec; from pathlib import Path; import subprocess
program = Path(find_spec("{ {'claude': 'claude_agent_sdk', 'codex': 'codex_cli_bin'}[name] }").origin).parent.parent / runtimes.INSIDE["{name}"][0]
print(".".join(map(str, runtimes._version(subprocess.run([str(program), "--version"], capture_output=True, text=True).stdout))))"""],
                          check=True, capture_output=True, text=True, cwd=root).stdout.strip()
    assert runs, f'no version from {name} --version'
    return {'version': version, 'runs': runs, 'url': wheel['url'], 'sha256': wheel['digests']['sha256']}


(root / 'build').mkdir(exist_ok=True)
(root / 'build' / 'runtimes.json').write_text(json.dumps({'claude': pin('claude', 'claude-agent-sdk'), 'codex': pin('codex', 'openai-codex-cli-bin')}, indent=1))
subprocess.run([
    python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--name', 'ken-engine',
    '--distpath', str(root / 'dist' / f'engine-{arch}'), '--workpath', str(root / 'build' / 'engine'),
    '--specpath', str(root / 'build'), '--paths', str(root),
    # Only data files the code can't import: Whisper's assets. Not the SDKs' own Claude Code and Codex.
    '--collect-data', 'faster_whisper', '--exclude-module', 'codex_cli_bin',
    *data('build/runtimes.json'),
    # Optional extras Ken never uses: Whisper's silence filter (off in voice.py) and a download accelerator.
    '--exclude-module', 'onnxruntime', '--exclude-module', 'hf_xet',
    *data('web', 'web'), *data('SOUL.template.md'), *data('jobs.default.json'),
    *data('skills', 'skills'), *data('video/kit', 'video/kit'), *data('data', 'data'),
    str(root / 'bot.py'),
], check=True, cwd=root)
