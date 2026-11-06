"""Ken's workspace tidies itself, so nothing piles up on a small disk (a Ken in the cloud) or a big one.

One rule: anything in Ken's workspace folders goes 7 days after it last changed. That's what people attach (inbox),
where Ken works (work), what it hands back (outbox) and each helper agent's files folder, all inside Ken's own
folder (~/.ken). Never the brain (memory, soul, skills), settings, logins, schedules, chat history or installed tools,
and never anything outside ~/.ken: a person's own files are only ever copied in. (Pieces of an upload that never
finished go after a day; nobody can use them.) Links are removed as links; what they point to is never touched.
"""
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("ken.workspace")

DAY = 86400
KEEP_DAYS, UPLOAD_DAYS = 7, 1
EVERY = 6 * 3600  # how often the sweep runs


def roots(home: Path) -> list[Path]:
    found = [home / "inbox", home / "work", home / "outbox"]
    agents = home / "agents"
    if agents.is_dir() and not agents.is_symlink():
        found += [a / "files" for a in agents.iterdir() if a.is_dir() and not a.is_symlink()]
    return [r for r in found if r.is_dir() and not r.is_symlink()]


def keep_days(path: Path, root: Path) -> int:
    return UPLOAD_DAYS if ".uploads" in path.relative_to(root).parts else KEEP_DAYS


def sweep(home: Path, now: float | None = None) -> dict:
    """Delete what's past its time in Ken's workspace folders. Returns what it did, for the log."""
    now = time.time() if now is None else now
    home = home.resolve()
    removed, freed = 0, 0
    for root in roots(home):
        root = root.resolve()
        if home not in root.parents:  # only ever inside Ken's own folder
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
            here = Path(dirpath)
            for name in filenames + [d for d in dirnames if (here / d).is_symlink()]:
                path = here / name
                try:
                    st = path.lstat()
                    if now - st.st_mtime <= keep_days(path, root) * DAY:
                        continue
                    size = 0 if path.is_symlink() else st.st_size
                    path.unlink()  # a link goes as a link
                    removed, freed = removed + 1, freed + size
                except (OSError, ValueError):
                    continue
            if here != root:
                try:
                    here.rmdir()  # only if it's now empty
                except OSError:
                    pass
    if removed:
        log.info("workspace sweep: removed %d files, freed %.1f MB", removed, freed / 1e6)
    return {"removed": removed, "freed_bytes": freed}


async def keep_tidy(home: Path) -> None:
    """Sweep now and every few hours, for as long as Ken runs."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(sweep, home)
        except Exception:
            log.exception("workspace sweep failed; trying again later")
        await asyncio.sleep(EVERY)
