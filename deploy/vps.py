#!/usr/bin/env python3
"""Fresh-machine deployment over SSH. Requires Python 3.9+ on the operator's Mac/Linux."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
# Deliberate allowlist: never upload .env, SSH keys, local memory, or arbitrary untracked files.
FILES = (
    "bot.py", "brains.py", "engine.py", "voice.py", "web.py", "install.sh", "ken", "ken.service.template", "ken.plist.template",
    "web/index.html", "web/app.js", "web/style.css", "web/manifest.webmanifest", "web/icon.svg",
    "web/vendor/preact-htm.js",
    "requirements.txt", "jobs.default.json", "SOUL.template.md", "README.md", "LICENSE",
    "recipes/README.md", "recipes/email.md", "deploy/README.md", "deploy/vps.py",
    "deploy/preflight.sh", "deploy/bootstrap.sh", "deploy/check.sh", "deploy/backup.sh",
    "deploy/restore.py",
)
SSH_OPTIONS = ["-o", "ForwardAgent=no", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15"]


def validate_host(value):
    if not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise argparse.ArgumentTypeError("Use root@IPv4, root@hostname, or an SSH config alias")
    return value


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise argparse.ArgumentTypeError("Use an IANA timezone such as Europe/Lisbon")
    return value


def bundle(root=ROOT):
    paths = [root / name for name in FILES]
    manifest = {}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path in sorted(paths):
            if path.is_symlink() or root.resolve() not in path.resolve().parents:
                raise ValueError(f"Refusing linked source: {path}")
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            manifest[name] = hashlib.sha256(data).hexdigest()
            info = tarfile.TarInfo(name)
            info.mode = 0o700 if name.endswith(".sh") or name == "ken" else 0o600
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        data = json.dumps(manifest, indent=2, sort_keys=True).encode()
        info = tarfile.TarInfo("release-manifest.json")
        info.mode, info.size = 0o600, len(data)
        archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def ssh(host, command, *, data=None, capture=False, terminal=False):
    args = ["ssh", *SSH_OPTIONS]
    args += ["-t"] if terminal else ["-o", "BatchMode=yes"]
    return subprocess.run(args + [host, command], input=data, check=True,
                          stdout=subprocess.PIPE if capture else None)


def prepare(args):
    payload = bundle()
    # Check before uploading: this command must never replace an existing configured Ken.
    ssh(args.host, "bash -s", data=(ROOT / "deploy/preflight.sh").read_bytes())
    staging = ssh(args.host, "mktemp -d /tmp/ken-deploy.XXXXXXXX", capture=True).stdout.decode().strip()
    if not re.fullmatch(r"/tmp/ken-deploy\.[A-Za-z0-9]+", staging):
        raise RuntimeError("Unexpected remote staging directory")
    quoted = shlex.quote(staging)
    try:
        ssh(args.host, f"tar -xzf - -C {quoted}", data=payload)
        ssh(args.host, f"bash {quoted}/deploy/bootstrap.sh {shlex.quote(args.timezone)}")
    finally:
        ssh(args.host, f"rm -rf -- {quoted}")
    print("Prepared. Next: python3 deploy/vps.py onboard " + args.host, flush=True)


def onboard(args):
    # Customer credentials are entered directly in their SSH terminal, never as CLI arguments.
    command = (
        'test "$(id -u)" = 0 && '
        'test -f /etc/ken-provisioned && '
        'test ! -e /home/ken/.ken/.env && '
        'ken_uid=$(id -u ken) && '
        'runuser -u ken -- env HOME=/home/ken USER=ken '
        'PATH=/home/ken/.local/bin:/usr/local/bin:/usr/bin:/bin '
        'XDG_RUNTIME_DIR=/run/user/$ken_uid '
        'KEN_HOSTED=1 KEN_AUTH_MODE=api KEN_SOURCE_DIR=/home/ken/.ken/app '
        'bash /home/ken/.ken/app/install.sh'
    )
    ssh(args.host, command, terminal=True)


def check(args):
    # Execute the operator's copy, never an agent-writable script as root.
    ssh(args.host, "bash -s", data=(ROOT / "deploy/check.sh").read_bytes())


def backup(args):
    target = Path(args.output).expanduser().resolve()
    if target.exists():
        raise ValueError("Backup destination already exists; choose a new filename")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Stream through SSH into a private file; no credentials are printed. The remote script
    # pauses Ken, archives its home, and restarts it even if tar fails.
    fd, temporary = tempfile.mkstemp(prefix=".ken-backup-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            subprocess.run(["ssh", *SSH_OPTIONS, "-o", "BatchMode=yes", args.host,
                            "bash -s"], input=(ROOT / "deploy/backup.sh").read_bytes(),
                           stdout=stream, check=True)
        with tarfile.open(temporary, "r:gz") as archive:
            if "./.ken/.env" not in archive.getnames():
                raise ValueError("Backup is missing the Ken configuration")
        # link is exclusive: a destination created during the download is never overwritten.
        os.link(temporary, target)
    finally:
        os.unlink(temporary)
    print(f"Backup saved to {target} (contains private files and credentials; mode 600).")
    print("See deploy/README.md for the isolated restore drill. This is not a full VM snapshot.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Install OS prerequisites and source on a fresh Ubuntu 24.04 box")
    p.add_argument("host", type=validate_host)
    p.add_argument("--timezone", required=True, type=validate_timezone)
    p.set_defaults(run=prepare)
    for name, fn in (("onboard", onboard), ("check", check)):
        p = commands.add_parser(name)
        p.add_argument("host", type=validate_host)
        p.set_defaults(run=fn)
    p = commands.add_parser("backup", help="Pause Ken briefly and download its home over SSH")
    p.add_argument("host", type=validate_host)
    p.add_argument("output")
    p.set_defaults(run=backup)
    args = parser.parse_args()
    try:
        args.run(args)
    except (subprocess.CalledProcessError, ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Deployment did not finish: {exc}\n")


if __name__ == "__main__":
    main()
