import os
import time

from workspace import DAY, sweep


def make(path, days_old, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - days_old * DAY
    os.utime(path, (t, t))
    return path


def test_sweep_keeps_the_brain_and_clears_old_work(tmp_path):
    home = tmp_path / ".ken"
    brain = [make(home / "memory" / "notes.md", 400), make(home / "SOUL.md", 400), make(home / "skills" / "x" / "SKILL.md", 400),
             make(home / ".env", 400), make(home / "jobs.json", 400), make(home / "app.db", 400),
             make(home / "tools" / "remotion-kit" / "big.bin", 400)]
    outside = make(tmp_path / "Movies" / "my-video.mov", 400)  # a person's own file: never touched

    raw_old = make(home / "inbox" / "IMG_1.MOV", 8)
    raw_new = make(home / "inbox" / "IMG_2.MOV", 1)
    cut_old = make(home / "work" / "videos" / "porto" / "cut.mp4", 8)
    reel_kept = make(home / "work" / "videos" / "river" / "reel.mp4", 5)
    cut_kept = make(home / "work" / "videos" / "river" / "cut.mp4", 6)
    reel_old = make(home / "work" / "videos" / "porto" / "reel.mp4", 8)
    sent_kept = make(home / "outbox" / "clip.mp4", 6)
    piece_old = make(home / "inbox" / ".uploads" / "abc.part", 2)
    doc_kept = make(home / "inbox" / "lease.pdf", 6)
    doc_old = make(home / "inbox" / "old.pdf", 8)
    agent_old = make(home / "agents" / "helper" / "files" / "voice.webm", 8)
    target = make(tmp_path / "kit" / "node_modules" / "pkg.js", 0)
    link = home / "work" / "videos" / "porto" / "node_modules"
    link.symlink_to(target.parent)
    old = time.time() - 9 * DAY
    if os.utime in os.supports_follow_symlinks:  # not on Windows; the sweep then goes by the folder's own time
        os.utime(link, (old, old), follow_symlinks=False)

    did = sweep(home)

    for p in brain + [outside, raw_new, reel_kept, cut_kept, sent_kept, doc_kept, target]:
        assert p.exists(), p
    for p in (raw_old, cut_old, reel_old, piece_old, doc_old, agent_old):
        assert not p.exists(), p
    assert not link.is_symlink() and target.exists()  # the link went, what it pointed to stayed
    assert not (home / "work" / "videos" / "porto").exists()  # emptied folders go too
    assert (home / "inbox").is_dir()  # the workspace folders themselves stay
    assert did["removed"] == 7  # six files and one link


def test_sweep_never_leaves_kens_folder_through_a_linked_workspace(tmp_path):
    home = tmp_path / ".ken"
    home.mkdir()
    elsewhere = make(tmp_path / "Documents" / "talk.mov", 400)
    (home / "work").symlink_to(elsewhere.parent)  # a workspace folder that is itself a link is skipped
    sweep(home)
    assert elsewhere.exists()
