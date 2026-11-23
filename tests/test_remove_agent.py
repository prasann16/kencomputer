"""Removing an agent: its chat really goes, the files it made are kept (Library or Removed chats) or deleted on
request, and the code folder it was pointed at is never touched."""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import library  # noqa: E402
from engine import HOME, REMOVED_CHATS, Engine  # noqa: E402
from test_engine import FakeBrain  # noqa: E402


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("KEN_CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(library, "on", lambda: False)
    (tmp_path / "ken").mkdir()
    e = Engine(tmp_path / "ken", FakeBrain())
    e.configure_home(tmp_path / "ken" / "work", lambda: "KEN SOUL", lambda: "KEN AT WORK")
    e.agents_dir.mkdir(parents=True)
    e.requests_dir.mkdir()
    return e


def made_agent(engine, tmp_path, path=""):
    """An agent with a chat, notes, keys, jobs, settings, sessions, and files it made."""
    a = engine.create_agent("Reels", path=path, context="Make my reels")
    pid, d = a["id"], engine.agents_dir / a["id"]
    (d / "memory" / "style.md").write_text("# Style\nshort")
    (d / "jobs.json").write_text(json.dumps([{"name": "daily", "time": "09:00"}]))
    (d / "tools.json").write_text("{}")
    (d / "files" / ".uploads").mkdir()
    (d / "files" / ".uploads" / "x.part").write_text("half")
    (d / "files" / "report.pdf").write_bytes(b"%PDF report")
    (d / "videos").mkdir()
    (d / "videos" / "cut.mp4").write_bytes(b"video" * 100)
    engine.log_message(pid, "you", "make a reel")
    engine.log_message(pid, "ken", "done")
    engine.set_key(pid, "API_KEY", "secret-value")
    engine.kv_set(f"session:{pid}", "claude-sid")
    engine.kv_set(f"session:codex:{pid}", "codex-sid")
    engine.kv_set("spent:claude-sid", "{}")
    engine.kv_set(f"thread:{pid}", "{}")
    engine.kv_set(f"backend:{pid}-2", "claude")  # another agent's: stays
    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(d))
    claude = tmp_path / "claude" / "projects" / enc
    claude.mkdir(parents=True)
    (claude / "claude-sid.jsonl").write_text("{}")
    day = tmp_path / "codex" / "sessions" / "2026" / "11" / "23"
    day.mkdir(parents=True)
    (day / "rollout-2026-11-23T10-00-00-codex-sid.jsonl").write_text('{"type":"session_meta","payload":{"cwd":"/elsewhere"}}\n')
    (day / "rollout-2026-11-23T11-00-00-run-sid.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {"cwd": os.path.realpath(d)}}) + "\n")
    (day / "rollout-2026-11-23T12-00-00-other.jsonl").write_text('{"type":"session_meta","payload":{"cwd":"/somewhere/else"}}\n')
    return pid, d, claude, day


async def test_bookkeeping_and_chat_are_deleted_and_files_kept(engine, tmp_path):
    pid, d, claude, day = made_agent(engine, tmp_path)
    assert engine.agent_files_size(pid) == {"count": 2, "bytes": len(b"%PDF report") + 500}
    done = await engine.remove_agent(pid)
    assert done["to"] == "folder" and done["files"] == 2
    assert not d.exists() and [a["id"] for a in engine.list_agents()] == []
    kept = engine.files_dir(HOME) / REMOVED_CHATS / "Reels"
    assert done["folder"] == str(kept)
    assert (kept / "report.pdf").read_bytes() == b"%PDF report" and (kept / "videos" / "cut.mp4").exists()
    assert sorted(p.name for p in kept.rglob("*") if p.is_file()) == ["cut.mp4", "report.pdf"]  # no notes, jobs, uploads
    assert engine.db.execute("SELECT COUNT(*) FROM events WHERE project=?", (pid,)).fetchone()[0] == 0
    for k in (f"session:{pid}", f"session:codex:{pid}", "spent:claude-sid", f"thread:{pid}"):
        assert engine.kv_get(k) is None
    assert engine.kv_get(f"backend:{pid}-2") == "claude"
    assert not (engine.home / "credentials" / "agents" / pid).exists()
    assert not claude.exists()
    assert sorted(f.name for f in day.iterdir()) == ["rollout-2026-11-23T12-00-00-other.jsonl"]
    assert not (engine.agents_dir / ".removed").exists()
    # The same name again starts fresh.
    again = engine.create_agent("Reels")
    assert again["id"] == pid and engine.messages(pid) == [] and engine.list_keys(pid) == []
    assert engine.kv_get(f"session:{pid}") is None


async def test_delete_files_deletes_them(engine, tmp_path):
    pid, d, _, _ = made_agent(engine, tmp_path)
    done = await engine.remove_agent(pid, delete_files=True)
    assert done == {"files": 0, "to": "deleted"}
    assert not d.exists() and not (engine.files_dir(HOME) / REMOVED_CHATS).exists()


async def test_the_code_folder_is_never_touched(engine, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.txt").write_text("mine")
    pid, d, _, _ = made_agent(engine, tmp_path, path=str(repo))
    (d / "files" / "repo-link").symlink_to(repo, target_is_directory=True)
    (d / "code").symlink_to(repo, target_is_directory=True)
    assert engine.agent_files_size(pid)["count"] == 2  # links aren't its files
    await engine.remove_agent(pid, delete_files=True)
    assert not d.exists()
    assert (repo / "keep.txt").read_text() == "mine" and sorted(p.name for p in repo.iterdir()) == ["keep.txt"]


async def test_a_second_removed_chat_of_the_same_name_gets_its_own_folder(engine, tmp_path):
    pid, _, _, _ = made_agent(engine, tmp_path)
    await engine.remove_agent(pid)
    a = engine.create_agent("Reels")
    (engine.agents_dir / a["id"] / "files" / "report.pdf").write_text("second")
    done = await engine.remove_agent(a["id"])
    assert Path(done["folder"]).name == "Reels 2" and (Path(done["folder"]) / "report.pdf").read_text() == "second"


async def test_with_the_library_files_go_up_and_failures_stay_visible(engine, tmp_path, monkeypatch):
    pid, d, _, _ = made_agent(engine, tmp_path)
    added = []

    def add(path):
        if path.name == "cut.mp4":
            raise RuntimeError("Your Library is full.")
        added.append((path.name, path.read_bytes()))
        return "abc"

    monkeypatch.setattr(library, "on", lambda: True)
    monkeypatch.setattr(library, "add", add)
    done = await engine.remove_agent(pid)
    assert done == {"files": 2, "to": "library"}
    assert not d.exists()
    await asyncio.gather(*engine._bg)
    assert added == [("report.pdf", b"%PDF report")]
    assert not (engine.home / "removing").exists() or not any((engine.home / "removing").iterdir())  # local copies gone
    kept = engine.files_dir(HOME) / REMOVED_CHATS / "Reels"
    assert [p.relative_to(kept).as_posix() for p in kept.rglob("*") if p.is_file()] == ["videos/cut.mp4"]


async def test_uploads_a_restart_stopped_carry_on(engine, tmp_path, monkeypatch):
    staging = engine.home / "removing" / "reels-1"
    (staging / "files").mkdir(parents=True)
    (staging / "chat.json").write_text(json.dumps({"name": "Reels"}))
    (staging / "files" / "a.txt").write_text("a")
    added = []
    monkeypatch.setattr(library, "add", lambda p: added.append(p.name) or "id")
    engine.start()
    try:
        for _ in range(100):
            if not staging.exists():
                break
            await asyncio.sleep(0.01)
        assert added == ["a.txt"] and not staging.exists()
    finally:
        for t in engine._bg:
            t.cancel()


async def test_busy_is_refused(engine, tmp_path):
    pid, d, _, _ = made_agent(engine, tmp_path)
    lock = engine.chat_locks.setdefault(pid, asyncio.Lock())
    async with lock:
        with pytest.raises(ValueError, match="working"):
            await engine.remove_agent(pid)
    engine.db.execute("INSERT INTO runs (id, project, title, prompt, source, status, created) VALUES ('r1', ?, 'Job', 'p', 'chat', 'running', 0)", (pid,))
    with pytest.raises(ValueError, match="working"):
        await engine.remove_agent(pid)
    assert d.exists() and engine.messages(pid)


async def test_removed_chats_are_never_swept(engine):
    from workspace import sweep
    kept = engine.files_dir(HOME) / REMOVED_CHATS / "Reels"
    kept.mkdir(parents=True)
    (kept / "old.pdf").write_text("x")
    (engine.files_dir(HOME) / "old.txt").write_text("y")
    for f in (kept / "old.pdf", engine.files_dir(HOME) / "old.txt"):
        os.utime(f, (0, 0))
    sweep(engine.home)
    assert (kept / "old.pdf").exists() and not (engine.files_dir(HOME) / "old.txt").exists()


async def test_the_web_api_takes_the_choice(engine, tmp_path, aiohttp_client):
    from web import WebApp

    pid, d, _, _ = made_agent(engine, tmp_path)
    app = WebApp(engine, tmp_path, rev="abc")
    client = await aiohttp_client(app.build())
    await client.get(f"/login?token={app.token}", allow_redirects=False)
    size = await (await client.get(f"/api/agents/{pid}/files-size")).json()
    assert size == {"count": 2, "bytes": len(b"%PDF report") + 500, "library": False}
    r = await client.delete(f"/api/agents/{pid}?files=delete")
    assert r.status == 200 and (await r.json())["to"] == "deleted"
    assert not d.exists() and (await client.get(f"/api/agents/{pid}/files-size")).status == 404
