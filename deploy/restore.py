#!/usr/bin/env python3
"""Extract a Ken home backup into a NEW directory for inspection/recovery. Python 3.12+."""
import argparse
import copy
import os
from pathlib import Path, PurePosixPath
import posixpath
import tarfile


def restore(archive_path, destination):
    destination = Path(destination)
    # Never merge into a live installation, even if the directory appears empty.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = []
            for original in archive.getmembers():
                member = copy.copy(original)
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Backup contains an unsafe file path")
                # Native Claude installs can use absolute links into this same home.
                # Rebase those links so an offline restore also works under /tmp.
                if member.issym() and member.linkname.startswith("/home/ken/"):
                    target = member.linkname[len("/home/ken/"):]
                    member.linkname = posixpath.relpath(target, str(path.parent))
                filtered = tarfile.data_filter(member, str(destination))
                if filtered is not None:
                    if str(path) == "." and filtered.isdir():
                        filtered.mode = 0o700
                    members.append(filtered)
            # Validate all entries before extracting. Apply data_filter again during
            # extraction so symlinks introduced by earlier entries are also checked.
            archive.extractall(destination, members=members, filter="data")
        config = destination / ".ken/.env"
        if not config.is_file():
            raise ValueError("Backup lacks .ken/.env; do not use it for recovery")
        config.chmod(0o600)
    except Exception:
        # Preserve the private destination for inspection; never delete an operator path.
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("destination", help="A directory that does not yet exist")
    args = parser.parse_args()
    if not hasattr(tarfile, "data_filter"):
        parser.error("Use Python 3.12 or newer for safe archive extraction")
    os.umask(0o077)
    try:
        result = restore(args.archive, args.destination)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"Restore did not finish: {exc}\n")
    print(f"Extracted to {result}. No service was started and no live installation was changed.")


if __name__ == "__main__":
    main()
