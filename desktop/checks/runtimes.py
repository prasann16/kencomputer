"""Inside the bundled engine (`ken-engine -c`) on a Mac with neither installed: Ken downloads Claude Code
and Codex (runtimes.py), checks them against the pinned checksums, and they run."""
import asyncio
import subprocess

import runtimes

runtimes.PLACES = []  # a runner may have its own copies; this checks the download

assert set(runtimes._pins()) == {"claude", "codex"}, runtimes._pins()
assert runtimes._pins()["claude"].get("runs", "").startswith("2."), "Claude Code is compared with the SDK version"
assert runtimes._pins()["codex"].get("runs"), "Codex is compared with its package version"
for name in ("claude", "codex"):
    assert runtimes.missing(name), f"{name} is still inside the app"
    asyncio.run(runtimes.ensure(name))
    program = runtimes._chosen[name]
    out = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=60, check=True).stdout.strip()
    print(f"{name}: {out} from {program}")
runtimes._chosen.clear()
runtimes.setup()  # the next start finds them without downloading again
assert set(runtimes._chosen) == {"claude", "codex"}, runtimes._chosen
assert runtimes.codex_bin() == runtimes._chosen["codex"]
